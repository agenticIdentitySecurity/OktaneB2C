'use client';

import { useEffect, useRef } from 'react';
import { Sparkles, ShieldCheck, Cpu } from 'lucide-react';
import type { Approval, ChatMessage, Intent } from '@/lib/types';
import MessageBubble from './MessageBubble';
import ChatComposer from './ChatComposer';
import PendingIntentCard from './PendingIntentCard';
import StepUpBanner from './StepUpBanner';

const SUGGESTIONS = [
  'What size basketball should I get for a 16-year-old?',
  "OK, purchase it when it's back in stock.",
  'What are my orders?',
];

export default function AssistantPanel({
  messages,
  intents,
  approval,
  approvalSummary,
  resumeUrl,
  onSend,
  busy,
  signedIn,
  llm,
}: {
  messages: ChatMessage[];
  intents: Intent[];
  approval: Approval | null;
  approvalSummary: string;
  resumeUrl: string;
  onSend: (message: string) => void;
  busy: boolean;
  signedIn: boolean;
  llm: string;
}) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, approval?.state]);

  return (
    <section className="flex h-[36rem] flex-col overflow-hidden rounded-2xl border border-neutral-border bg-primary/40">
      <div className="flex items-center gap-2.5 border-b border-neutral-border px-4 py-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-okta-blue/15">
          <Sparkles className="h-4 w-4 text-okta-blue-light" />
        </div>
        <div className="leading-tight">
          <div className="font-display text-sm font-semibold">Shopping assistant</div>
          <div className="text-[11px] text-net-white/40">
            Acts on your behalf — never on its own authority
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="hidden items-center gap-1.5 rounded-full border border-neutral-border px-2.5 py-1 font-mono text-[10px] text-net-white/40 sm:flex">
            <Cpu className="h-3 w-3" />
            {llm}
          </span>
          <span className="flex items-center gap-1.5 rounded-full border border-okta-blue/30 bg-okta-blue/10 px-2.5 py-1 text-[10px] text-okta-blue-light">
            <ShieldCheck className="h-3 w-3" />
            Scoped access
          </span>
        </div>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
        {messages.length === 0 && (
          <div className="mx-auto mt-10 max-w-lg text-center">
            <Sparkles className="mx-auto h-7 w-7 text-okta-blue-light/60" />
            <h3 className="mt-3 font-display text-base font-semibold">
              Ask about gear, sizing, or your orders
            </h3>
            <p className="mt-1.5 text-xs leading-relaxed text-net-white/45">
              {signedIn
                ? 'The assistant reads catalog and inventory data with a token scoped to just that. Buying anything needs your explicit approval.'
                : 'Sign in first — the assistant only ever acts with your identity, never its own.'}
            </p>
          </div>
        )}

        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}

        {intents
          .filter((i) => i.state === 'PENDING_STOCK')
          .map((intent) => (
            <PendingIntentCard key={intent.intent_id} intent={intent} />
          ))}

        {approval && (
          <StepUpBanner
            approval={approval}
            summary={approvalSummary}
            resumeUrl={resumeUrl}
          />
        )}

        <div ref={endRef} />
      </div>

      <ChatComposer
        onSend={onSend}
        disabled={busy || !signedIn}
        placeholder={
          signedIn ? 'Ask about sizing, stock, or your orders…' : 'Sign in to chat'
        }
        suggestions={signedIn ? SUGGESTIONS : []}
      />
    </section>
  );
}
