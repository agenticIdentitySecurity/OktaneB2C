export function formatPrice(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

/**
 * Render a Unix-seconds timestamp as "HH:MM:SS · DD Mon" in the shopper's
 * locale. Used by the security trace to make `exp`, `iat`, and `auth_time`
 * readable at a glance instead of raw ten-digit integers.
 */
export function formatUnix(ts: number): string {
  if (!Number.isFinite(ts) || ts <= 0) return String(ts);
  const d = new Date(ts * 1000);
  const time = d.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
  const day = d.toLocaleDateString([], { day: '2-digit', month: 'short' });
  return `${time} · ${day}`;
}

/** Timestamps that formatUnix should be applied to when they appear as claim values. */
export const TIME_CLAIM_KEYS = new Set(['exp', 'iat', 'auth_time', 'nbf']);
