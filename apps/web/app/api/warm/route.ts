import { NextResponse } from 'next/server';
import { agentFetch } from '@/lib/agent';

/**
 * Wake the backend services. Fired by the storefront on mount so the
 * free-tier Render containers are warm before the shopper clicks anything.
 * Always returns 200 — a slow warm-up is not a user-visible failure.
 */
export async function GET() {
  try {
    const upstream = await agentFetch('/warm');
    if (upstream.ok) {
      return NextResponse.json(await upstream.json());
    }
    return NextResponse.json({ agent: false, mcp: false }, { status: 200 });
  } catch {
    return NextResponse.json({ agent: false, mcp: false }, { status: 200 });
  }
}
