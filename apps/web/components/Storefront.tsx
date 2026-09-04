'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  Approval,
  ChatMessage,
  ChatTurn,
  Intent,
  Product,
  Profile,
  TraceEvent,
} from '@/lib/types';
import Header from './Header';
import ProductGrid from './ProductGrid';
import AssistantPanel from './AssistantPanel';
import RestockTriggerButton from './RestockTriggerButton';
import TelemetryDrawer from './TelemetryDrawer';

const SETTLED = ['COMPLETED', 'DENIED', 'EXPIRED', 'FAILED'];
const DEFAULT_SKU = 'CE-BB-GAME-7';

interface RaisedApproval {
  approval_id: string;
  summary: string;
  resume_url: string;
}

let messageSeq = 0;
const nextId = () => `m${++messageSeq}`;

export default function Storefront({
  initialProducts,
  initialProfile,
  shoppers,
  demoMode,
}: {
  initialProducts: Product[];
  initialProfile: Profile | null;
  shoppers: Profile[];
  demoMode: boolean;
}) {
  const [products, setProducts] = useState(initialProducts);
  const [profile, setProfile] = useState(initialProfile);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [intents, setIntents] = useState<Intent[]>([]);
  const [chatTrace, setChatTrace] = useState<TraceEvent[]>([]);
  const [approvalTrace, setApprovalTrace] = useState<TraceEvent[]>([]);
  const [raised, setRaised] = useState<RaisedApproval | null>(null);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [llm, setLlm] = useState('deterministic');
  const settledRef = useRef(false);

  const refreshProducts = useCallback(async () => {
    const response = await fetch('/api/catalog', { cache: 'no-store' });
    if (response.ok) setProducts((await response.json()).products);
  }, []);

  const say = useCallback((content: string) => {
    setMessages((prev) => [...prev, { id: nextId(), role: 'assistant', content }]);
  }, []);

  // Fire-and-forget prewarm of the Render free-tier backends. Without this a
  // cold Simulate-restock click sees a 502 from the MCP server while it spins
  // up. See mcp_client._request_with_retry — retries absorb the same case for
  // any request that races the cold start; this just makes it not happen.
  useEffect(() => {
    fetch('/api/warm').catch(() => {});
  }, []);

  async function signIn(email: string) {
    const response = await fetch('/api/session', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    if (!response.ok) {
      say('Sign-in failed. Is the agent service running?');
      return;
    }
    setProfile((await response.json()).profile);
  }

  async function signOut() {
    await fetch('/api/session', { method: 'DELETE' });
    setProfile(null);
    setMessages([]);
    setIntents([]);
    setChatTrace([]);
    setApprovalTrace([]);
    setRaised(null);
    setApproval(null);
  }

  async function send(message: string) {
    const pendingId = nextId();
    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: 'user', content: message },
      { id: pendingId, role: 'assistant', content: 'checking with Okta…', pending: true },
    ]);
    setBusy(true);

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ message }),
      });
      const body = await response.json();

      if (!response.ok) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === pendingId
              ? { ...m, pending: false, content: body.error ?? 'something went wrong' }
              : m,
          ),
        );
        if (response.status === 401) setProfile(null);
        return;
      }

      const turn = body as ChatTurn;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingId ? { ...m, pending: false, content: turn.reply } : m,
        ),
      );
      setIntents(turn.pending_intents ?? []);
      setLlm(turn.llm);
      if (turn.trace?.length) {
        setChatTrace((prev) => [...prev, ...turn.trace]);
        setDrawerOpen(true);
      }
    } finally {
      setBusy(false);
    }
  }

  async function restock() {
    const sku = intents[0]?.variant_sku ?? DEFAULT_SKU;
    const response = await fetch('/api/restock', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ sku, stock: 12 }),
    });
    if (!response.ok) {
      say('The restock trigger failed. Is the agent service running?');
      return;
    }

    const body = await response.json();
    await refreshProducts();

    const first = (body.approvals_raised ?? [])[0] as RaisedApproval | undefined;
    if (!first) {
      say(`${sku} is back in stock. No standing order was waiting on it.`);
      return;
    }

    settledRef.current = false;
    setRaised(first);
    setApproval(null);
    say(
      "Good news — it's back in stock. Before I buy anything I need you to approve " +
        'it and confirm it is really you.',
    );
    setDrawerOpen(true);
  }

  // Poll the approval the way a CIBA client would, until it settles.
  useEffect(() => {
    if (!raised) return;
    const id = encodeURIComponent(raised.approval_id);
    let cancelled = false;

    async function tick() {
      const [stateResponse, traceResponse] = await Promise.all([
        fetch(`/api/approvals/${id}`, { cache: 'no-store' }),
        fetch(`/api/approvals/${id}/telemetry`, { cache: 'no-store' }),
      ]);
      if (cancelled || !stateResponse.ok) return;

      const { approval: current, intent } = await stateResponse.json();
      setApproval(current);
      if (intent) {
        setIntents((prev) =>
          prev.map((i) => (i.intent_id === intent.intent_id ? intent : i)),
        );
      }
      if (traceResponse.ok) setApprovalTrace((await traceResponse.json()).trace ?? []);

      if (SETTLED.includes(current.state) && !settledRef.current) {
        settledRef.current = true;
        await refreshProducts();
        say(
          current.state === 'COMPLETED'
            ? `Done — order ${current.order_id} is placed in your name. I only had permission to do that because you just approved it.`
            : current.state === 'DENIED'
              ? 'Understood, I have not bought anything. The standing order is closed.'
              : `The approval ended as ${current.state}. Nothing was purchased.`,
        );
      }
    }

    tick();
    const timer = setInterval(() => {
      if (!settledRef.current) tick();
    }, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [raised, refreshProducts, say]);

  return (
    <>
      <Header
        profile={profile}
        shoppers={shoppers}
        onSignIn={signIn}
        onSignOut={signOut}
      />

      <main className="mx-auto max-w-7xl space-y-8 px-6 py-8">
        <ProductGrid products={products}>
          {demoMode && <RestockTriggerButton onRestock={restock} />}
        </ProductGrid>

        <AssistantPanel
          messages={messages}
          intents={intents}
          approval={raised ? approval : null}
          approvalSummary={raised?.summary ?? ''}
          resumeUrl={raised?.resume_url ?? '#'}
          onSend={send}
          busy={busy}
          signedIn={profile !== null}
          llm={llm}
        />
      </main>

      <TelemetryDrawer
        events={[...chatTrace, ...approvalTrace]}
        open={drawerOpen}
        onToggle={() => setDrawerOpen(!drawerOpen)}
        onClear={() => {
          setChatTrace([]);
          setApprovalTrace([]);
        }}
      />
    </>
  );
}
