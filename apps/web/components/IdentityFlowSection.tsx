'use client';

import type { TraceEvent, TraceKind } from '@/lib/types';
import { formatUnix } from '@/lib/format';

/**
 * A compact top-of-drawer view of the identity chain, styled to make the
 * two-leg exchange (user token → ID-JAG → access token → tool call) legible
 * at a glance. The full accordion below still shows every event with claims.
 *
 * Colour mapping mirrors the reference token inspector, remapped onto the
 * OktaneB2C palette so nothing new gets invented here.
 */

interface SectionMeta {
  label: string;
  dotClass: string;
  textClass: string;
}

const SECTIONS: Partial<Record<TraceKind, SectionMeta>> = {
  user_token: {
    label: 'User ID token',
    dotClass: 'bg-okta-blue-light',
    textClass: 'text-okta-blue-light',
  },
  id_jag: {
    label: 'ID-JAG assertion',
    dotClass: 'bg-tech-purple',
    textClass: 'text-tech-purple',
  },
  access_token: {
    label: 'Scoped access token',
    dotClass: 'bg-tech-purple-light',
    textClass: 'text-tech-purple-light',
  },
  mcp_call: {
    label: 'MCP tool call',
    dotClass: 'bg-success-green',
    textClass: 'text-success-green',
  },
  mcp_denied: {
    label: 'MCP tool refused',
    dotClass: 'bg-error-red',
    textClass: 'text-error-red',
  },
  stepup: {
    label: 'Step-up',
    dotClass: 'bg-accent',
    textClass: 'text-accent',
  },
};

const KIND_ORDER: TraceKind[] = [
  'stepup',
  'user_token',
  'id_jag',
  'access_token',
  'mcp_call',
  'mcp_denied',
];

function detailFor(event: TraceEvent): string {
  const c = event.claims ?? {};
  if (event.kind === 'access_token') {
    const scp = Array.isArray(c.scp) ? (c.scp as string[]).join(' ') : String(c.scp ?? '');
    return `aud=${c.aud ?? '?'}${scp ? ` · scp=${scp}` : ''}`;
  }
  if (event.kind === 'id_jag') {
    return `aud=${c.aud ?? '?'}`;
  }
  if (event.kind === 'user_token') {
    const acr = c.acr ? ` · acr=${c.acr}` : '';
    return `sub=${c.sub ?? '?'}${acr}`;
  }
  if (event.kind === 'mcp_call' || event.kind === 'mcp_denied') {
    return String(c.tool ?? event.detail ?? '');
  }
  if (event.kind === 'stepup') {
    return event.detail;
  }
  return event.detail;
}

export default function IdentityFlowSection({ events }: { events: TraceEvent[] }) {
  const flow = events.filter((e) => KIND_ORDER.includes(e.kind));
  if (flow.length === 0) return null;

  return (
    <section
      aria-labelledby="identity-flow-heading"
      className="mb-5 rounded-lg border border-neutral-border bg-neutral-bg/60 p-3"
    >
      <h3
        id="identity-flow-heading"
        className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-net-white/60"
      >
        Identity flow
      </h3>
      <ol className="space-y-1.5">
        {flow.map((event, i) => {
          const meta = SECTIONS[event.kind];
          const okColor = event.ok ? meta?.dotClass : 'bg-error-red';
          const labelColor = event.ok ? meta?.textClass : 'text-error-red';
          return (
            <li
              key={`${event.at}-${i}`}
              className="flex items-baseline gap-2 font-mono text-[11px] leading-relaxed"
            >
              <span
                className={[
                  'inline-block h-2 w-2 shrink-0 translate-y-[1px] rounded-full',
                  okColor ?? 'bg-net-white/30',
                ].join(' ')}
                aria-hidden
              />
              <span
                className={[
                  'shrink-0 font-display text-[11px] font-medium',
                  labelColor ?? 'text-net-white/70',
                ].join(' ')}
              >
                {meta?.label ?? event.kind}
              </span>
              <span className="min-w-0 break-all text-net-white/60">{detailFor(event)}</span>
              <span className="ml-auto shrink-0 text-[10px] text-net-white/25">
                {formatUnix(event.at)}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
