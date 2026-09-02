'use client';

import { useEffect, useState } from 'react';
import { Activity, X, Trash2 } from 'lucide-react';
import type { TraceEvent } from '@/lib/types';
import TraceStep from './TraceStep';
import IdentityFlowSection from './IdentityFlowSection';

interface WhoAmI {
  agent_client_id: string;
  workload_principal_id: string;
  demo_mode: string;
  token_exchange_impl: string;
}

/**
 * Plain language for the non-technical half of the room. The claims are right
 * there underneath for everyone else.
 */
function summarize(events: TraceEvent[]): string[] {
  if (events.length === 0) return [];

  const lines: string[] = [];
  const exchanges = events.filter((e) => e.kind === 'id_jag').length;
  const audiences = new Set(
    events
      .filter((e) => e.kind === 'access_token')
      .map((e) => String(e.claims?.aud ?? ''))
      .filter(Boolean),
  );
  const scopes = new Set(
    events
      .filter((e) => e.kind === 'access_token')
      .flatMap((e) => (Array.isArray(e.claims?.scp) ? (e.claims.scp as string[]) : [])),
  );
  const tools = events
    .filter((e) => e.kind === 'mcp_call')
    .map((e) => String(e.claims?.tool ?? ''))
    .filter(Boolean);
  const denied = events.filter((e) => e.kind === 'mcp_denied');
  const stepups = events.filter((e) => e.kind === 'stepup');

  // Tokens are cached per (sub, aud, scope), so a repeat run has scopes but no
  // fresh exchange. Neither line may assume the other was pushed.
  if (scopes.size > 0) {
    lines.push(
      `The assistant traded your sign-in for ${
        exchanges > 0 ? `${exchanges} ` : ''
      }short-lived ${
        exchanges === 1 ? 'permission' : 'permissions'
      }, limited to ${[...scopes].join(', ')}${
        audiences.size > 1 ? ` across ${audiences.size} separate APIs` : ''
      }.`,
    );
  }
  if (tools.length > 0) {
    lines.push(
      `It read only what it needed: ${[...new Set(tools)].join(', ')}. Every call was checked by the API, not by the assistant.`,
    );
  }
  if (stepups.some((e) => e.ok)) {
    lines.push(
      'You proved it was really you with a second factor before any money moved.',
    );
  }
  if (denied.length > 0 || stepups.some((e) => !e.ok)) {
    lines.push(
      `${denied.length + stepups.filter((e) => !e.ok).length} attempt(s) were refused. That is the point: the assistant cannot talk its way past the check.`,
    );
  }
  return lines;
}

export default function TelemetryDrawer({
  events,
  open,
  onToggle,
  onClear,
}: {
  events: TraceEvent[];
  open: boolean;
  onToggle: () => void;
  onClear: () => void;
}) {
  const summary = summarize(events);
  const [who, setWho] = useState<WhoAmI | null>(null);

  useEffect(() => {
    if (!open || who) return;
    fetch('/api/whoami')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setWho(d as WhoAmI))
      .catch(() => {});
  }, [open, who]);

  return (
    <>
      {!open && (
        <button
          type="button"
          onClick={onToggle}
          className="fixed right-0 top-1/3 z-40 flex items-center gap-2 rounded-l-lg border border-r-0 border-tech-purple/40 bg-tech-purple/15 px-3 py-3 text-xs font-medium text-tech-purple-light backdrop-blur-md hover:bg-tech-purple/25"
        >
          <Activity className="h-4 w-4" />
          <span className="[writing-mode:vertical-rl]">Security trace</span>
          {events.length > 0 && (
            <span className="rounded-full bg-tech-purple px-1.5 py-0.5 text-[10px] font-bold text-neutral-bg">
              {events.length}
            </span>
          )}
        </button>
      )}

      <aside
        className={[
          'fixed right-0 top-0 z-50 flex h-full w-full max-w-md flex-col border-l border-tech-purple/30 bg-primary/95 backdrop-blur-md transition-transform duration-200',
          open ? 'translate-x-0' : 'translate-x-full',
        ].join(' ')}
        aria-hidden={!open}
      >
        <div className="flex items-center gap-2 border-b border-neutral-border px-4 py-3.5">
          <Activity className="h-4 w-4 text-tech-purple-light" />
          <h2 className="font-display text-sm font-semibold">Security trace</h2>
          <span className="text-[11px] text-net-white/35">{events.length} steps</span>
          <button
            type="button"
            onClick={onClear}
            className="ml-auto rounded-md p-1.5 text-net-white/40 hover:text-net-white"
            aria-label="Clear trace"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={onToggle}
            className="rounded-md p-1.5 text-net-white/50 hover:text-net-white"
            aria-label="Close security trace"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {who && (
          <div className="flex items-center gap-2 border-b border-neutral-border/60 bg-neutral-bg/40 px-4 py-2 font-mono text-[10px] text-net-white/50">
            <span className="text-net-white/35">agent</span>
            <span className="text-tech-purple-light">{who.agent_client_id}</span>
            <span className="ml-auto flex items-center gap-1.5">
              <span
                className={[
                  'inline-block h-1.5 w-1.5 rounded-full',
                  who.demo_mode === 'mock' ? 'bg-accent' : 'bg-okta-blue-light',
                ].join(' ')}
                aria-hidden
              />
              <span className="uppercase tracking-[0.08em] text-net-white/60">
                {who.demo_mode}
              </span>
            </span>
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-4 py-4">
          {events.length === 0 ? (
            <p className="text-xs leading-relaxed text-net-white/40">
              Ask the assistant something. Every token it obtains and every call it
              makes shows up here, in order.
            </p>
          ) : (
            <>
              {summary.length > 0 && (
                <div className="mb-5 rounded-lg border border-tech-purple/25 bg-tech-purple/10 p-3">
                  <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-tech-purple-light">
                    What just happened
                  </div>
                  <ul className="space-y-1.5 text-xs leading-relaxed text-net-white/70">
                    {summary.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                </div>
              )}
              <IdentityFlowSection events={events} />
              <ol className="space-y-4 border-l border-neutral-border/70 pl-0">
                {events.map((event, i) => (
                  <TraceStep key={`${event.at}-${i}`} event={event} index={i} />
                ))}
              </ol>
            </>
          )}
        </div>
      </aside>
    </>
  );
}
