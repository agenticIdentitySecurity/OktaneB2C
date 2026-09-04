'use client';

import { useState } from 'react';
import { Copy, Check, ChevronDown } from 'lucide-react';
import type { TraceEvent } from '@/lib/types';
import { formatUnix, TIME_CLAIM_KEYS } from '@/lib/format';

/**
 * Decoded claims for one step in the chain.
 *
 * `sub` is the human and `act.sub` / `cid` is the agent. That contrast is the
 * whole "on behalf of" story in one screenshot, so those two rows are
 * highlighted and everything else is muted.
 *
 * A "Show raw JSON" toggle reveals the full claims dict verbatim so a
 * technical viewer can inspect anything the summary elides.
 */

const HUMAN_KEYS = new Set(['sub']);
const AGENT_KEYS = new Set(['act.sub', 'cid', 'client_id', 'placed_by_agent']);

function renderValue(key: string, value: unknown): string {
  if (TIME_CLAIM_KEYS.has(key) && typeof value === 'number') {
    return `${formatUnix(value)}  (${value})`;
  }
  if (Array.isArray(value)) return value.join(' ');
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export default function TokenChip({ event }: { event: TraceEvent }) {
  const [rawOpen, setRawOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const entries = Object.entries(event.claims ?? {});
  if (entries.length === 0) return null;

  const isToken =
    event.kind === 'id_jag' ||
    event.kind === 'access_token' ||
    event.kind === 'user_token';

  const rawJson = JSON.stringify(event.claims, null, 2);

  async function copyRaw() {
    try {
      await navigator.clipboard.writeText(rawJson);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // clipboard unavailable (insecure context, denied permission) — silently no-op
    }
  }

  return (
    <div className="mt-2 rounded-md border border-tech-purple/25 bg-neutral-bg/70 p-2.5">
      <dl className="space-y-1">
        {entries.map(([key, value]) => {
          const human = HUMAN_KEYS.has(key);
          const agent = AGENT_KEYS.has(key);
          return (
            <div key={key} className="flex gap-2 font-mono text-[11px] leading-relaxed">
              <dt
                className={[
                  'w-[86px] shrink-0 text-right',
                  human
                    ? 'text-accent'
                    : agent
                      ? 'text-tech-purple-light'
                      : 'text-net-white/35',
                ].join(' ')}
              >
                {key}
              </dt>
              <dd
                className={[
                  'break-all',
                  human || agent ? 'text-net-white' : 'text-net-white/60',
                ].join(' ')}
              >
                {renderValue(key, value)}
                {human && (
                  <span className="ml-1.5 text-[10px] text-accent/70">the shopper</span>
                )}
                {agent && (
                  <span className="ml-1.5 text-[10px] text-tech-purple-light/70">
                    the agent
                  </span>
                )}
              </dd>
            </div>
          );
        })}
      </dl>

      <div className="mt-2 border-t border-tech-purple/15 pt-1.5">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setRawOpen(!rawOpen)}
            className="inline-flex items-center gap-1 rounded px-1 text-[10px] font-medium uppercase tracking-[0.08em] text-tech-purple-light/70 hover:text-tech-purple-light"
            aria-expanded={rawOpen}
          >
            <ChevronDown
              className={[
                'h-3 w-3 transition-transform',
                rawOpen ? 'rotate-0' : '-rotate-90',
              ].join(' ')}
            />
            {rawOpen ? 'Hide raw JSON' : 'Show raw JSON'}
          </button>
          {isToken && (
            <span className="ml-auto font-mono text-[10px] text-net-white/25">
              signature verified, value elided
            </span>
          )}
        </div>
        {rawOpen && (
          <div className="mt-1.5 rounded border border-neutral-border bg-neutral-bg p-2">
            <div className="mb-1 flex items-center gap-2">
              <span className="text-[10px] uppercase tracking-[0.08em] text-net-white/35">
                claims
              </span>
              <button
                type="button"
                onClick={copyRaw}
                className="ml-auto inline-flex items-center gap-1 rounded px-1 text-[10px] text-net-white/50 hover:text-net-white"
                aria-label="Copy claims JSON"
              >
                {copied ? (
                  <>
                    <Check className="h-3 w-3 text-success-green" />
                    <span className="text-success-green">copied</span>
                  </>
                ) : (
                  <>
                    <Copy className="h-3 w-3" />
                    <span>copy</span>
                  </>
                )}
              </button>
            </div>
            <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-net-white/70">
              {rawJson}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
