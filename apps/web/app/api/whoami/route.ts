import { NextResponse } from 'next/server';
import { agentFetch } from '@/lib/agent';

/**
 * Public identity of the agent — used by the security trace drawer header.
 * Static, cheap, no auth needed. Proxied so the browser talks to one origin.
 */
export async function GET() {
  try {
    const upstream = await agentFetch('/agent/whoami');
    if (!upstream.ok) {
      return NextResponse.json(
        { error: 'agent unreachable', status: upstream.status },
        { status: 502 },
      );
    }
    return NextResponse.json(await upstream.json());
  } catch (err) {
    return NextResponse.json(
      { error: 'agent unreachable', detail: String(err) },
      { status: 502 },
    );
  }
}
