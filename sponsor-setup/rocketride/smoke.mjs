// Run from the workspace: node --env-file=.env sponsor-setup/rocketride/smoke.mjs
// Validates first, checks existing credits, and runs one text echo. No model calls.
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { RocketRideClient } from 'rocketride';

const input = 'Hackathon setup: RocketRide text echo verified.';
const uri = process.env.ROCKETRIDE_URI;
const apiKey = process.env.ROCKETRIDE_APIKEY;
let client;
let taskToken;
let taskStartAttempted = false;
let exactEchoVerified = false;
let stage = 'configuration';

try {
  if (!uri || !apiKey) {
    console.error('RocketRide: configure ROCKETRIDE_URI and ROCKETRIDE_APIKEY in the workspace .env.');
    process.exitCode = 2;
  } else {
    if (new URL(uri).hostname !== 'staging.rocketride.ai') {
      throw new Error('This smoke test targets the documented staging server only.');
    }
    const pipeline = JSON.parse(await readFile(new URL('./echo.pipe', import.meta.url), 'utf8'));
    // Keep the execution bounded even if someone edits the neighboring pipeline.
    if (!Array.isArray(pipeline.components) || pipeline.components.length !== 2
        || pipeline.components[0].provider !== 'webhook'
        || pipeline.components[1].provider !== 'response_text') {
      throw new Error('Expected the two-node webhook/text-response smoke pipeline.');
    }
    client = new RocketRideClient({
      uri, auth: apiKey, persist: false, requestTimeout: 15_000,
      // This pipeline uses no substitutions; do not forward unrelated workspace secrets.
      env: {},
    });
    stage = 'authentication';
    await client.connect(apiKey, { timeout: 10_000 });
    console.log('RocketRide: authenticated staging connection passed.');

    stage = 'pipeline validation';
    const validation = await client.validate({ pipeline });
    if (!Array.isArray(validation.errors) || !Array.isArray(validation.warnings)) {
      throw new Error('Validation returned an unexpected response shape.');
    }
    if (validation.errors.length) {
      console.error(`RocketRide: echo pipeline has ${validation.errors.length} validation error(s).`);
      process.exitCode = 1;
    } else {
      console.log(`RocketRide: echo pipeline validation passed (${validation.warnings.length} warning(s)).`);
      stage = 'existing credit balance check';
      const orgId = client.getOrgId();
      if (!orgId) {
        throw new Error('An organization is required to check the existing credit balance.');
      }
      const creditBalance = await client.billing.getCreditBalance(orgId);
      const balances = creditBalance?.balances;
      if (!balances || typeof balances !== 'object' || Array.isArray(balances)
          || Object.values(balances).some(value => typeof value !== 'number' || !Number.isFinite(value))) {
        throw new Error('Credit balance returned an unexpected response shape.');
      }
      const hasCredit = Object.values(balances).some(value => value > 0);
      if (!hasCredit) {
        console.log('RocketRide: execution BLOCKED because no positive existing credit balance is available.');
        console.log('Authentication and pipeline validation passed; no task was started.');
        process.exitCode = 2;
      } else {
        console.log('RocketRide: positive existing credits found; starting one tiny echo task.');
        stage = 'pipeline start';
        // A preselected token also allows cleanup after an ambiguous start response.
        taskToken = randomUUID();
        taskStartAttempted = true;
        const started = await client.use({
          pipeline, token: taskToken, ttl: 60, threads: 1,
          name: 'Hackathon setup text echo', useExisting: false,
        });
        if (!started || typeof started.token !== 'string' || !started.token) {
          throw new Error('The server did not return a task token.');
        }
        taskToken = started.token;
        stage = 'text echo';
        const result = await client.send(taskToken, input, {}, 'text/plain');
        if (!result || result.result_types?.text !== 'text'
            || !Array.isArray(result.text)
            || result.text.some(segment => typeof segment !== 'string')
            || result.text.join('') !== input) {
          throw new Error('The returned text did not exactly match the synthetic input.');
        }
        exactEchoVerified = true;
      }
    }
  }
} catch (error) {
  // Avoid account identities, API keys, task tokens, server responses, and raw URLs.
  const name = error instanceof Error && /^[A-Za-z]+$/.test(error.name) ? error.name : 'Error';
  console.error(`RocketRide: smoke check failed during ${stage} (${name}).`);
  process.exitCode = 1;
} finally {
  if (client && taskToken && taskStartAttempted) {
    try {
      await client.terminate(taskToken);
      console.log('RocketRide: temporary task terminated.');
    } catch {
      console.error('RocketRide: termination could not be confirmed; the task has a 60-second idle TTL.');
      process.exitCode = 1;
    }
  }
  if (client) {
    try {
      await client.disconnect();
    } catch {
      console.error('RocketRide: connection cleanup could not be confirmed.');
      process.exitCode = 1;
    }
  }
}

if (exactEchoVerified && !process.exitCode) {
  console.log('RocketRide PASS: live two-node pipeline returned the exact synthetic text; no LLM was used.');
}
