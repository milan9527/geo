import {
  BedrockAgentCoreClient,
  CreatePaymentSessionCommand,
  GetPaymentInstrumentBalanceCommand,
  GetPaymentSessionCommand,
  InvokeAgentRuntimeCommand,
  ListPaymentInstrumentsCommand,
} from "@aws-sdk/client-bedrock-agentcore";
import { randomUUID } from "node:crypto";

const REGION = process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || "us-east-1";
const PAYMENT_MANAGER_ARN =
  process.env.AGENTCORE_PAYMENT_MANAGER_ARN ||
  "arn:aws:bedrock-agentcore:us-east-1:632930644527:payment-manager/demopaymentmanager-zy4lsroxj3";
const PAYMENT_CONNECTOR_ID =
  process.env.AGENTCORE_PAYMENT_CONNECTOR_ID || "mystripeprivyconnector3-wbtiy89xwz";
const PAYMENT_USER_ID = process.env.AGENTCORE_PAYMENT_USER_ID || "demo-user";
const PAYMENT_RUNTIME_ARN =
  process.env.AGENTCORE_PAYMENT_RUNTIME_ARN ||
  "arn:aws:bedrock-agentcore:us-east-1:632930644527:runtime/PaymentCrawler_PaymentCrawler-99d3LsDP4r";
const TARGET_URL =
  process.env.X402_TEST_URL ||
  "https://sandbox.node4all.com/v1/x402-test";
const MAX_SESSION_SPEND_USD = process.env.X402_MAX_SESSION_SPEND_USD || "0.01";
const MAX_CHALLENGE_BASE_UNITS = BigInt(
  process.env.X402_MAX_CHALLENGE_BASE_UNITS || "2000",
);
const EXPECTED_NETWORK = "eip155:84532";
const ALLOWED_UNPROBED_TARGETS = new Set([
  "https://sandbox.node4all.com/v1/x402-test",
]);

const shouldPay = process.argv.includes("--pay");
const walletOnly = process.argv.includes("--wallet-only");
const verifySessionArg = process.argv.indexOf("--verify-session");
const sessionIdToVerify =
  verifySessionArg >= 0 ? process.argv[verifySessionArg + 1] : undefined;
const client = new BedrockAgentCoreClient({ region: REGION });

function decodeBase64Json(value) {
  return JSON.parse(Buffer.from(value, "base64").toString("utf8"));
}

function findAcceptedPayment(challenge) {
  const options = challenge.accepts || (challenge.accepted ? [challenge.accepted] : []);
  return options.find(
    (option) =>
      option?.network === EXPECTED_NETWORK &&
      option?.extra?.name === "USDC" &&
      option?.amount !== undefined,
  );
}

function redact(text) {
  return text
    .replace(
      /(PAYMENT-SIGNATURE|payment-signature)["']?\s*[:=]\s*["']?[A-Za-z0-9+/=_-]+/gi,
      "$1: [REDACTED]",
    )
    .replace(/\b[A-Za-z0-9+/]{300,}={0,2}\b/g, "[REDACTED_BASE64]");
}

function extractSseText(body) {
  const fragments = [];
  for (const line of body.split(/\r?\n/)) {
    if (!line.startsWith("data:")) {
      continue;
    }
    try {
      const event = JSON.parse(line.slice(5).trim());
      const text = event?.event?.contentBlockDelta?.delta?.text;
      if (typeof text === "string") {
        fragments.push(text);
      }
    } catch {
      // Ignore non-JSON event-stream lines.
    }
  }
  return fragments.join("");
}

async function inspectChallenge() {
  let response;
  try {
    response = await fetch(TARGET_URL, {
      method: "GET",
      redirect: "manual",
      headers: { "user-agent": "Aperture-GEO-x402-test/1.0" },
    });
  } catch (error) {
    const normalizedTarget = new URL(TARGET_URL).href;
    if (
      shouldPay &&
      ALLOWED_UNPROBED_TARGETS.has(normalizedTarget) &&
      error?.cause?.code === "ENOTFOUND"
    ) {
      console.log(
        JSON.stringify(
          {
            phase: "challenge",
            localProbe: "unavailable",
            reason: "local DNS is restricted",
            target: normalizedTarget,
            enforcedSessionLimitUsd: MAX_SESSION_SPEND_USD,
          },
          null,
          2,
        ),
      );
      return;
    }
    throw error;
  }

  if (response.status !== 402) {
    throw new Error(`Expected HTTP 402, received ${response.status}`);
  }

  const encoded = response.headers.get("payment-required");
  if (!encoded) {
    throw new Error("HTTP 402 response did not include payment-required");
  }

  const challenge = decodeBase64Json(encoded);
  const accepted = findAcceptedPayment(challenge);
  if (!accepted) {
    throw new Error(`No USDC payment option for ${EXPECTED_NETWORK}`);
  }

  const amount = BigInt(accepted.amount);
  if (amount > MAX_CHALLENGE_BASE_UNITS) {
    throw new Error(
      `Challenge amount ${accepted.amount} exceeds limit ${MAX_CHALLENGE_BASE_UNITS}`,
    );
  }

  const challengedUrl = challenge.resource?.url;
  if (!challengedUrl || new URL(challengedUrl).protocol !== "https:") {
    throw new Error("Challenge resource URL is missing or is not HTTPS");
  }

  console.log(
    JSON.stringify(
      {
        phase: "challenge",
        statusCode: response.status,
        protocolVersion: challenge.x402Version,
        network: accepted.network,
        token: accepted.extra?.name,
        amountBaseUnits: accepted.amount,
        maxSessionSpendUsd: MAX_SESSION_SPEND_USD,
        payTo: accepted.payTo,
      },
      null,
      2,
    ),
  );
}

async function findWallet() {
  const response = await client.send(
    new ListPaymentInstrumentsCommand({
      paymentManagerArn: PAYMENT_MANAGER_ARN,
      paymentConnectorId: PAYMENT_CONNECTOR_ID,
      userId: PAYMENT_USER_ID,
      agentName: "aperture-geo-x402-test",
      maxResults: 20,
    }),
  );

  const instrument = response.paymentInstruments?.find(
    (candidate) =>
      candidate.status === "ACTIVE" &&
      candidate.paymentInstrumentType === "EMBEDDED_CRYPTO_WALLET",
  );
  if (!instrument?.paymentInstrumentId) {
    throw new Error("No ACTIVE embedded crypto wallet was found");
  }

  const balance = await client.send(
    new GetPaymentInstrumentBalanceCommand({
      paymentManagerArn: PAYMENT_MANAGER_ARN,
      paymentConnectorId: PAYMENT_CONNECTOR_ID,
      paymentInstrumentId: instrument.paymentInstrumentId,
      userId: PAYMENT_USER_ID,
      agentName: "aperture-geo-x402-test",
      chain: "BASE_SEPOLIA",
      token: "USDC",
    }),
  );

  console.log(
    JSON.stringify(
      {
        phase: "wallet",
        instrumentId: instrument.paymentInstrumentId,
        status: instrument.status,
        connectorId: instrument.paymentConnectorId,
        balance: balance.tokenBalance,
      },
      null,
      2,
    ),
  );
  return instrument.paymentInstrumentId;
}

async function createPaymentSession() {
  const response = await client.send(
    new CreatePaymentSessionCommand({
      paymentManagerArn: PAYMENT_MANAGER_ARN,
      userId: PAYMENT_USER_ID,
      agentName: "aperture-geo-x402-test",
      limits: {
        maxSpendAmount: {
          value: MAX_SESSION_SPEND_USD,
          currency: "USD",
        },
      },
      expiryTimeInMinutes: 15,
      clientToken: randomUUID(),
    }),
  );
  const session = response.paymentSession;
  if (!session?.paymentSessionId) {
    throw new Error("AgentCore did not return a payment session ID");
  }

  console.log(
    JSON.stringify(
      {
        phase: "session",
        paymentSessionId: session.paymentSessionId,
        maxSpend: session.limits?.maxSpendAmount,
        availableSpend: session.availableLimits?.availableSpendAmount,
        expiryTimeInMinutes: session.expiryTimeInMinutes,
      },
      null,
      2,
    ),
  );
  return session.paymentSessionId;
}

async function verifyPaymentSession(paymentSessionId) {
  if (!paymentSessionId) {
    throw new Error("--verify-session requires a payment session ID");
  }
  const response = await client.send(
    new GetPaymentSessionCommand({
      paymentManagerArn: PAYMENT_MANAGER_ARN,
      paymentSessionId,
      userId: PAYMENT_USER_ID,
      agentName: "aperture-geo-x402-test",
    }),
  );
  const session = response.paymentSession;
  console.log(
    JSON.stringify(
      {
        phase: "session-verification",
        paymentSessionId: session?.paymentSessionId,
        maxSpend: session?.limits?.maxSpendAmount,
        availableSpend: session?.availableLimits?.availableSpendAmount,
        expiryTimeInMinutes: session?.expiryTimeInMinutes,
      },
      null,
      2,
    ),
  );
}

async function invokePaymentCrawler(instrumentId, paymentSessionId) {
  const payload = {
    prompt:
      `Fetch ${TARGET_URL} using http_request. ` +
      "Pay the x402 charge if required. Return the final HTTP status, " +
      "payment transaction hash, network, and complete paid JSON response.",
    payment_user_id: PAYMENT_USER_ID,
    payment_instrument_id: instrumentId,
    payment_session_id: paymentSessionId,
  };

  const response = await client.send(
    new InvokeAgentRuntimeCommand({
      agentRuntimeArn: PAYMENT_RUNTIME_ARN,
      qualifier: "DEFAULT",
      runtimeSessionId: randomUUID(),
      contentType: "application/json",
      accept: "application/json",
      payload: Buffer.from(JSON.stringify(payload)),
    }),
  );

  const body = response.response
    ? await response.response.transformToString()
    : "";
  const safeBody = redact(body);
  const reconstructedText = redact(extractSseText(body));
  const searchableText = `${safeBody}\n${reconstructedText}`;
  const transactions = [...searchableText.matchAll(/\b0x[a-fA-F0-9]{64}\b/g)].map(
    (match) => match[0],
  );
  const status200 =
    /\b(?:HTTP Status|HTTP status|statusCode)["':\s*]+200\b/i.test(searchableText) ||
    /successfully (?:fetched|retrieved|settled)/i.test(reconstructedText);

  console.log(
    JSON.stringify(
      {
        phase: "paid-fetch",
        invokeStatusCode: response.statusCode,
        contentType: response.contentType,
        paidHttp200Observed: status200,
        transactionHashes: [...new Set(transactions)],
        responsePreview: reconstructedText.slice(-4000),
      },
      null,
      2,
    ),
  );

  if (response.statusCode !== 200 || !transactions.length) {
    throw new Error("Paid crawl did not return a confirmed x402 transaction");
  }
}

async function main() {
  const instrumentId = await findWallet();
  if (verifySessionArg >= 0) {
    await verifyPaymentSession(sessionIdToVerify);
    console.log(
      JSON.stringify({ phase: "complete", verificationOnly: true, success: true }),
    );
    return;
  }
  if (walletOnly) {
    console.log(JSON.stringify({ phase: "complete", walletOnly: true, success: true }));
    return;
  }

  await inspectChallenge();

  if (!shouldPay) {
    console.log(
      JSON.stringify({
        phase: "complete",
        paid: false,
        message: "Probe succeeded. Re-run with --pay to perform the testnet payment.",
      }),
    );
    return;
  }

  const paymentSessionId = await createPaymentSession();
  await invokePaymentCrawler(instrumentId, paymentSessionId);
  console.log(JSON.stringify({ phase: "complete", paid: true, success: true }));
}

main().catch((error) => {
  console.error(
    JSON.stringify(
      {
        phase: "error",
        name: error?.name || "Error",
        message: redact(String(error?.message || error)),
      },
      null,
      2,
    ),
  );
  process.exitCode = 1;
});
