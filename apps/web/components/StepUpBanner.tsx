'use client';

import {
  MailCheck,
  Fingerprint,
  CircleCheck,
  CircleX,
  Loader2,
  TriangleAlert,
} from 'lucide-react';
import type { Approval } from '@/lib/types';

/**
 * Human-readable explanations for the check-name a step-up rejection returns.
 * Kept in sync with apps/agent/app/approvals/stepup.py::complete — those are
 * the seven check codes that can appear in approval.failure.
 */
const FAILURE_HINTS: Array<[RegExp, string]> = [
  [/nonce_mismatch/, 'The ID token was not minted for this approval request.'],
  [/subject_mismatch/, 'This approval belongs to a different shopper — a forwarded link cannot approve it.'],
  [/acr_absent/, 'No acr claim on the token. An unrecognised acr_values is ignored silently, so an absent claim is treated as failure.'],
  [/acr_insufficient/, 'The second factor did not satisfy the required assurance level.'],
  [/auth_time_absent/, 'No auth_time claim, so we cannot prove the sign-in is fresh.'],
  [/auth_time_stale/, 'The authentication happened before the approval was requested, so it cannot be consent for it.'],
  [/auth_time_expired/, 'The authentication is older than the freshness window (120s).'],
  [/invalid_state/, 'The step-up state is unknown, expired, or already consumed.'],
  [/invalid_resume_link/, 'The resume link has already been used or the approval has moved on.'],
  [/missing_code/, 'No authorization code came back on the callback.'],
];

function failureHint(failure: string): string | null {
  for (const [pattern, hint] of FAILURE_HINTS) {
    if (pattern.test(failure)) return hint;
  }
  return null;
}

/**
 * Beat 7, inline in the thread. The agent is blocked and polling — exactly the
 * shape CIBA has, so real CIBA can replace the notifier without touching this.
 *
 * The resume link is surfaced because a demo has no inbox. It is not an
 * authorization: it names an approval and nothing more, and step-up still gates
 * the order.
 */
export default function StepUpBanner({
  approval,
  summary,
  resumeUrl,
}: {
  approval: Approval | null;
  summary: string;
  resumeUrl: string;
}) {
  const state = approval?.state ?? 'NOTIFIED';
  const settled = ['COMPLETED', 'DENIED', 'EXPIRED', 'FAILED'].includes(state);
  const stepupFailed = state === 'STEPUP_FAILED';

  const tone =
    state === 'COMPLETED'
      ? 'border-success-green/40 bg-success-green/10'
      : state === 'DENIED' || state === 'EXPIRED' || state === 'FAILED'
        ? 'border-error-red/40 bg-error-red/10'
        : stepupFailed
          ? 'border-error-red/40 bg-error-red/5'
          : 'border-okta-blue/40 bg-okta-blue/10';

  const hint = approval?.failure ? failureHint(approval.failure) : null;

  return (
    <div className={['ml-11 max-w-md rounded-xl border p-3.5', tone].join(' ')}>
      <div className="flex items-center gap-2">
        {state === 'COMPLETED' ? (
          <CircleCheck className="h-4 w-4 text-success-green" />
        ) : settled ? (
          <CircleX className="h-4 w-4 text-error-red" />
        ) : state === 'STEPUP_VERIFIED' ? (
          <Fingerprint className="h-4 w-4 text-okta-blue-light" />
        ) : (
          <Loader2 className="h-4 w-4 animate-spin text-okta-blue-light" />
        )}
        <span className="text-xs font-semibold uppercase tracking-[0.12em] text-okta-blue-light">
          {state === 'COMPLETED'
            ? 'Purchase approved'
            : state === 'DENIED'
              ? 'Purchase declined'
              : state === 'EXPIRED'
                ? 'Approval expired'
                : state === 'FAILED'
                  ? 'Order failed'
                  : 'Waiting for you'}
        </span>
        <span className="ml-auto font-mono text-[10px] text-net-white/30">{state}</span>
      </div>

      <div className="mt-2.5 text-sm">{summary}</div>

      {!settled && (
        <>
          <p className="mt-2 text-[11px] leading-relaxed text-net-white/50">
            Back in stock. The assistant cannot spend your money on its own, so it
            has asked you to approve this purchase and verify it is really you.
          </p>
          <a
            href={resumeUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-3 inline-flex items-center gap-2 rounded-lg bg-okta-blue px-3 py-2 text-xs font-semibold text-net-white hover:bg-okta-blue-light"
          >
            <MailCheck className="h-3.5 w-3.5" />
            Open the notification
          </a>
          <div className="mt-2 font-mono text-[10px] text-net-white/30">
            expires in {approval?.seconds_remaining ?? 900}s · a second factor is
            required
          </div>
        </>
      )}

      {state === 'COMPLETED' && approval?.order_id && (
        <p className="mt-2 font-mono text-[11px] text-success-green">
          {approval.order_id} · acr {approval.verified_acr}
        </p>
      )}
      {approval?.failure && (
        <div className="mt-3 rounded-md border border-error-red/25 bg-error-red/5 p-2.5">
          <div className="flex items-start gap-2">
            <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0 text-error-red" />
            <div className="min-w-0 flex-1">
              <div className="font-mono text-[10px] uppercase tracking-[0.08em] text-error-red">
                Step-up refused
              </div>
              <p className="mt-1 break-all font-mono text-[11px] text-error-red/90">
                {approval.failure}
              </p>
              {hint && (
                <p className="mt-1.5 text-[11px] leading-relaxed text-net-white/60">
                  {hint}
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
