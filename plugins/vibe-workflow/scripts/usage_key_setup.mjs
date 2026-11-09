// The trusted setup flow obtains approval before sending the connector's encrypted result.
import { spawnSync } from 'node:child_process';
import { webcrypto } from 'node:crypto';
import { isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';

const INPUT_LIMIT = 4096;
const WAIT_MS = 120_000;
const WRITER_WAIT_MS = 15_000;
const identity = /^[A-Za-z0-9_-]{1,128}$/;
const writer = fileURLToPath(new URL('../runtime/usage_credentials.py', import.meta.url));

async function encryptedResult() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    const newline = chunk.indexOf(10);
    size += chunk.length;
    if (size > INPUT_LIMIT) break;
    if (newline === -1) {
      chunks.push(chunk);
      continue;
    }
    if (newline !== chunk.length - 1) break;
    chunks.push(chunk.subarray(0, newline));
    const result = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (result?.version !== 1 || Object.keys(result).length !== 2 ||
        typeof result.ciphertext !== 'string' || !/^[A-Za-z0-9_-]{683}$/.test(result.ciphertext)) break;
    const bytes = Buffer.from(result.ciphertext, 'base64url');
    if (bytes.toString('base64url') !== result.ciphertext) break;
    return bytes;
  }
  throw new Error();
}

async function setup() {
  const [organization, project, python] = process.argv.slice(2);
  // The trusted host supplies the same interpreter that will later resolve the item.
  if (process.argv.length !== 5 || !identity.test(organization) || !identity.test(project) ||
      !isAbsolute(python) || python.includes('\0')) {
    process.stderr.write('invalid_credential_binding\n');
    process.exitCode = 1;
    return;
  }

  const started = performance.now();
  const timer = setTimeout(() => {
    process.stderr.write('credential_setup_timeout\n');
    process.exit(1);
  }, WAIT_MS);
  let reason = 'credential_setup_failed';
  let secret;
  try {
    const { subtle } = webcrypto;
    const { publicKey, privateKey } = await subtle.generateKey({
      name: 'RSA-OAEP', modulusLength: 4096,
      publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256',
    }, false, ['encrypt', 'decrypt']);
    const { kty, n, e } = await subtle.exportKey('jwk', publicKey);
    process.stdout.write(JSON.stringify({ recipient_public_key_jwk: { kty, n, e } }) + '\n');
    const ciphertext = await encryptedResult();
    secret = Buffer.from(await subtle.decrypt('RSA-OAEP', privateKey, ciphertext));
    const remaining = Math.floor(WAIT_MS - (performance.now() - started));
    if (remaining <= 0 || !secret.length || secret.length > 512) throw new Error();

    reason = 'credential_storage_failed';
    const result = spawnSync(python, ['-I', '-B', writer, organization, project], {
      input: secret, stdio: ['pipe', 'pipe', 'ignore'], maxBuffer: INPUT_LIMIT,
      timeout: Math.min(remaining, WRITER_WAIT_MS), killSignal: 'SIGKILL',
    });
    if (result.error || result.status !== 0) throw new Error();
    const receipt = JSON.parse(result.stdout.toString('utf8'));
    if (typeof receipt?.credential_ref !== 'string' || Object.keys(receipt).length !== 1 ||
        !/^vcw_[0-9a-f]{32}$/.test(receipt.credential_ref)) throw new Error();
    process.stdout.write(JSON.stringify({ credential_ref: receipt.credential_ref }) + '\n');
  } catch {
    process.stderr.write(reason + '\n');
    process.exitCode = 1;
  } finally {
    secret?.fill(0);
    clearTimeout(timer);
  }
}

await setup();
