import { RocketRideClient } from 'rocketride';

const uri = process.env.ROCKETRIDE_URI || 'https://staging.rocketride.ai';
const apiKey = process.env.ROCKETRIDE_APIKEY;
let client;
const deadline = setTimeout(() => {
  console.error('RocketRide: check timed out; connection is not verified.');
  process.exit(1);
}, 25_000);

try {
  await RocketRideClient.getServerInfo(uri, 10_000);
  console.log('RocketRide: server probe passed.');
  if (!apiKey) {
    console.log('RocketRide: SDK installed; authentication and pipeline execution are NOT verified.');
    console.log('Next: pnpm exec rocketride init --uri https://staging.rocketride.ai');
    process.exitCode = 2;
  } else {
    client = new RocketRideClient({ uri, auth: apiKey, persist: false, requestTimeout: 10_000 });
    await client.connect(undefined, { timeout: 10_000 });
    console.log('RocketRide: authenticated connection passed. No pipeline was started.');
  }
} catch (error) {
  // Never print a server response, credential, or full connection URL.
  console.error(`RocketRide: check failed (${error?.name || 'Error'}).`);
  process.exitCode = 1;
} finally {
  if (client) await client.disconnect().catch(() => {});
  clearTimeout(deadline);
}
