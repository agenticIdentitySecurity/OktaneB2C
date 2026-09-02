#!/usr/bin/env node
// Generate the agent's private_key_jwt keypair.
//
// Cross App Access authenticates the agent by signature, so this key *is* the
// agent's identity. The private half goes into apps/agent/.env and is never
// committed; only the public half is ever registered with Okta or handed to
// Terraform.
//
//   node scripts/gen-agent-key.mjs

import { generateKeyPairSync, randomBytes } from 'node:crypto';

const kid = `oktane-agent-${randomBytes(6).toString('hex')}`;

const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const priv = { ...privateKey.export({ format: 'jwk' }), kid, alg: 'RS256', use: 'sig' };
const pub = { ...publicKey.export({ format: 'jwk' }), kid, alg: 'RS256', use: 'sig' };

console.log(`# Key id: ${kid}
#
# 1. Add this to apps/agent/.env (one line, single-quoted — it is gitignored):

OKTA_AGENT_KEY_ID=${kid}
OKTA_AGENT_PRIVATE_KEY_JWK='${JSON.stringify(priv)}'

# 2. Register only the public half with Okta — as the agent app's JWKS, or as
#    the agent_public_jwk Terraform variable. Never the private one.

${JSON.stringify(pub, null, 2)}
`);
