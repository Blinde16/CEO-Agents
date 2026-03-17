"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type ClientConfig = {
  client_id: string;
  display_name?: string | null;
  timezone: string;
  working_hours: string;
  priority_contacts: string[];
  focus_blocks?: string[];
  voice_examples?: string[];
};

type ConversationContext = {
  intent: string;
  action_type?: string | null;
  collected_fields: Record<string, unknown>;
  missing_fields: string[];
};

type DraftProposal = {
  kind: "email" | "calendar";
  title: string;
  summary: string;
  details: Array<{ label: string; value: string }>;
  warnings: string[];
  source: string;
  confidence_label: string;
  confidence_score: number;
  action_type: string;
  approval_required: boolean;
  payload: Record<string, unknown>;
};

type EmailTriageResult = {
  message_id: string;
  subject: string;
  sender: string;
  date: string;
  category: string;
  urgency_score: number;
  summary: string;
  action_items: string[];
  proposed_meeting_time: string | null;
  proposed_meeting_attendees: string[];
  requires_reply: boolean;
  reply_deadline: string | null;
};

type MeetingBriefing = {
  event_id: string;
  event_title: string;
  start_time: string;
  attendees: string[];
  relationship_context: string;
  open_items: string[];
  suggested_talking_points: string[];
  recent_emails: Array<{ from: string; subject: string; snippet: string }>;
};

type ConversationResponse = {
  state: string;
  assistant_message: string;
  context: ConversationContext;
  proposal?: DraftProposal | null;
  triage_results?: EmailTriageResult[];
};

type ApprovalRecord = {
  approval_id: string;
  action_id: string;
  client_id: string;
  status: string;
};

type ActionRecord = {
  action_id: string;
  status: string;
  approval_status: string;
  result?: Record<string, unknown> | null;
};

type IntegrationRecord = {
  client_id: string;
  provider: string;
  status: string;
  connected_account?: string | null;
};

type Message = {
  id: string;
  role: "assistant" | "user";
  text: string;
  proposal?: DraftProposal;
  triageResults?: EmailTriageResult[];
  briefing?: MeetingBriefing;
  result?: Record<string, unknown> | null;
  emphasis?: "normal" | "success" | "warning";
};

const SUGGESTED_PROMPTS = [
  "Prep me for my next meeting.",
  "What's on my calendar tomorrow?",
  "When am I free for a 45 minute meeting this week?",
  "Triage my inbox and summarize what needs attention.",
  "Reply to Sarah and let her know Thursday afternoon works.",
  "Schedule a 1:1 with Sarah next week about partnership planning.",
];

const BRIEFING_PROMPT = "Prep me for my next meeting.";

const demoClient: ClientConfig = {
  client_id: "acme-ceo",
  display_name: "Acme Executive Office",
  timezone: "America/Denver",
  working_hours: "08:00-17:00",
  priority_contacts: ["Sarah Chen", "Board Chair"],
  focus_blocks: ["09:00-11:00"],
};

const DEMO_USER_ID = "blake-demo";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

function buildId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function formatExecutionSummary(actionType: string, result?: Record<string, unknown> | null) {
  if (result?.error) {
    return `I couldn't complete that cleanly: ${String(result.error)}`;
  }
  if (!result) return "Done. I recorded the approval.";
  if (actionType === "draft_email_reply") {
    return "That looks good. I created the Gmail draft and saved it to the connected account.";
  }
  if (actionType === "create_event") {
    return `That looks good. I created the calendar event for ${String(result.requested_time ?? "the requested time")} (${String(result.duration_minutes ?? 30)} min).`;
  }
  if (actionType === "reschedule_event") {
    return `Done. I rescheduled to ${String(result.requested_time ?? "the requested time")}.`;
  }
  if (actionType === "cancel_event") return "Done. I cancelled the meeting.";
  return "Done. I executed the approved action.";
}

function ConfidenceBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = pct >= 85 ? "#22c55e" : pct >= 65 ? "#f59e0b" : "#ef4444";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 12,
        fontSize: 11,
        fontWeight: 600,
        background: color + "22",
        color,
        border: `1px solid ${color}44`,
      }}
    >
      {pct}% confidence
    </span>
  );
}

function TriagePanel({
  results,
  onBookMeeting,
  onDraftReply,
}: {
  results: EmailTriageResult[];
  onBookMeeting: (r: EmailTriageResult) => void;
  onDraftReply: (r: EmailTriageResult) => void;
}) {
  const categoryColors: Record<string, string> = {
    urgent: "#ef4444",
    action_required: "#f59e0b",
    meeting_request: "#6366f1",
    fyi: "#64748b",
    newsletter: "#94a3b8",
  };

  return (
    <div style={{ marginTop: 12 }}>
      {results.slice(0, 8).map((r) => {
        const color = categoryColors[r.category] ?? "#64748b";
        return (
          <div
            key={r.message_id}
            style={{
              border: `1px solid ${color}44`,
              borderLeft: `3px solid ${color}`,
              borderRadius: 8,
              padding: "10px 14px",
              marginBottom: 8,
              background: color + "08",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
              <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color, letterSpacing: 0.5 }}>
                {r.category.replace("_", " ")}
              </span>
              <span style={{ fontSize: 10, color: "#94a3b8" }}>urgency {r.urgency_score}/5</span>
              {r.requires_reply && (
                <span style={{ fontSize: 10, color: "#f59e0b", fontWeight: 600 }}>↩ reply needed</span>
              )}
              {r.reply_deadline && (
                <span style={{ fontSize: 10, color: "#ef4444" }}>by {r.reply_deadline}</span>
              )}
            </div>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 2 }}>{r.subject}</div>
            <div style={{ fontSize: 12, color: "#64748b", marginBottom: 6 }}>From {r.sender}</div>
            <div style={{ fontSize: 12, lineHeight: 1.5, marginBottom: 6 }}>{r.summary}</div>
            {r.action_items.length > 0 && (
              <ul style={{ margin: "4px 0 6px 14px", padding: 0, fontSize: 12, color: "#475569" }}>
                {r.action_items.slice(0, 2).map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            )}
            {r.proposed_meeting_time && (
              <div style={{ fontSize: 12, color: "#6366f1", marginBottom: 6 }}>
                Proposed time: {r.proposed_meeting_time}
                {r.proposed_meeting_attendees.length > 0 && ` with ${r.proposed_meeting_attendees.join(", ")}`}
              </div>
            )}
            <div style={{ display: "flex", gap: 8 }}>
              {r.requires_reply && (
                <button
                  onClick={() => onDraftReply(r)}
                  style={{
                    fontSize: 11,
                    padding: "3px 10px",
                    borderRadius: 6,
                    border: "1px solid #6366f1",
                    background: "transparent",
                    color: "#6366f1",
                    cursor: "pointer",
                  }}
                >
                  Draft reply
                </button>
              )}
              {r.proposed_meeting_time && (
                <button
                  onClick={() => onBookMeeting(r)}
                  style={{
                    fontSize: 11,
                    padding: "3px 10px",
                    borderRadius: 6,
                    border: "1px solid #22c55e",
                    background: "transparent",
                    color: "#22c55e",
                    cursor: "pointer",
                  }}
                >
                  Book this meeting
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function BriefingPanel({
  briefing,
  onDraftFollowUp,
}: {
  briefing: MeetingBriefing;
  onDraftFollowUp?: (prompt: string) => void;
}) {
  const firstAttendee = briefing.attendees[0]
    ? briefing.attendees[0].split("@")[0].replace(".", " ")
    : "the attendee";

  return (
    <div
      style={{
        border: "1px solid #6366f144",
        borderLeft: "3px solid #6366f1",
        borderRadius: 8,
        padding: "12px 16px",
        marginTop: 8,
        background: "#6366f108",
      }}
    >
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8, color: "#6366f1" }}>
        Pre-meeting briefing — {briefing.event_title}
      </div>
      <div style={{ fontSize: 12, marginBottom: 8, lineHeight: 1.6 }}>
        <strong>Relationship:</strong> {briefing.relationship_context}
      </div>
      {briefing.open_items.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5, color: "#94a3b8", marginBottom: 4 }}>
            Open items
          </div>
          <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12 }}>
            {briefing.open_items.map((item, i) => <li key={i}>{item}</li>)}
          </ul>
        </div>
      )}
      {briefing.suggested_talking_points.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5, color: "#94a3b8", marginBottom: 4 }}>
            Suggested talking points
          </div>
          <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12 }}>
            {briefing.suggested_talking_points.map((pt, i) => <li key={i}>{pt}</li>)}
          </ul>
        </div>
      )}
      {onDraftFollowUp && (
        <button
          className="ghost-button"
          type="button"
          style={{ fontSize: 12, marginTop: 4 }}
          onClick={() =>
            onDraftFollowUp(
              `Draft a follow-up email to ${firstAttendee} after our "${briefing.event_title}" meeting, summarizing next steps.`,
            )
          }
        >
          Draft follow-up email →
        </button>
      )}
    </div>
  );
}

export default function HomePage() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "assistant",
      text: "What do you want me to handle? I can triage your inbox, draft email replies, or coordinate calendar changes — then show you a preview before anything is sent.",
    },
  ]);
  const [context, setContext] = useState<ConversationContext | null>(null);
  const [pendingProposal, setPendingProposal] = useState<DraftProposal | null>(null);
  const [revisionProposal, setRevisionProposal] = useState<DraftProposal | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [clientReady, setClientReady] = useState(false);
  const [integrations, setIntegrations] = useState<IntegrationRecord[]>([]);
  const [briefing, setBriefing] = useState<MeetingBriefing | null>(null);
  const threadEndRef = useRef<HTMLDivElement>(null);

  function pushAssistantMessage(message: Omit<Message, "id" | "role">) {
    setMessages((current) => [
      ...current,
      { id: buildId(), role: "assistant", ...message },
    ]);
  }

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  useEffect(() => {
    async function bootstrap() {
      setBusy(true);
      setError(null);
      try {
        await api<ClientConfig>("/clients", {
          method: "POST",
          body: JSON.stringify({
            ...demoClient,
            scheduling_preferences: {},
            approval_rules: {
              email_tone: "concise and warm",
            },
          }),
        });
        const existingIntegrations = await api<IntegrationRecord[]>(
          `/integrations?client_id=${encodeURIComponent(demoClient.client_id)}`,
        );
        setIntegrations(existingIntegrations);
        setClientReady(true);
      } catch (bootstrapError) {
        setError(bootstrapError instanceof Error ? bootstrapError.message : "Failed to prepare demo workspace.");
      } finally {
        setBusy(false);
      }
    }
    bootstrap();
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const integration = params.get("integration");
    const status = params.get("status");
    const message = params.get("message");

    if (integration === "google" && status === "connected") {
      pushAssistantMessage({
        text: "Google is connected. I can now read your real inbox and calendar, create Gmail drafts, and book calendar events for approved actions.",
        emphasis: "success",
      });
      api<IntegrationRecord[]>(`/integrations?client_id=${encodeURIComponent(demoClient.client_id)}`)
        .then((records) => setIntegrations(records))
        .catch(() => null);
    }
    if (integration === "google" && status === "error") {
      setError(message || "Google connection failed.");
    }
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = input.trim();
    if (!message || busy || !clientReady) return;

    const userMessage: Message = { id: buildId(), role: "user", text: message };
    setMessages((current) => [...current, userMessage]);
    setInput("");
    setBusy(true);
    setError(null);
    setBriefing(null);

    try {
      if (revisionProposal) {
        const originalPrompt = String(revisionProposal.payload.source_text ?? "").trim();
        const revisionAction = await api<ActionRecord>("/actions", {
          method: "POST",
          body: JSON.stringify({
            client_id: demoClient.client_id,
            user_id: DEMO_USER_ID,
            action_type: revisionProposal.action_type,
            payload: revisionProposal.payload,
          }),
        });

        if (revisionAction.approval_status === "pending") {
          const approvals = await api<ApprovalRecord[]>(`/approvals?client_id=${encodeURIComponent(demoClient.client_id)}`);
          const matchingApproval = approvals.find(
            (approval) => approval.action_id === revisionAction.action_id && approval.status === "pending",
          );

          if (matchingApproval) {
            await api<ApprovalRecord>("/approvals/decision", {
              method: "POST",
              body: JSON.stringify({
                approval_id: matchingApproval.approval_id,
                reviewer_id: DEMO_USER_ID,
                decision: "rejected",
                feedback: message,
              }),
            });
          }
        }

        const response = await api<ConversationResponse>("/assistant/respond", {
          method: "POST",
          body: JSON.stringify({
            client_id: demoClient.client_id,
            user_id: DEMO_USER_ID,
            message: originalPrompt || String(revisionProposal.title),
            context: null,
          }),
        });

        setContext(response.context);
        setPendingProposal(response.proposal ?? null);
        setRevisionProposal(null);
        pushAssistantMessage({
          text: response.assistant_message,
          proposal: response.proposal ?? undefined,
          triageResults: response.triage_results?.length ? response.triage_results : undefined,
        });
        return;
      }

      // Briefing prompt short-circuits directly to /briefing/next
      if (message === BRIEFING_PROMPT) {
        const result = await api<MeetingBriefing>(
          `/briefing/next?client_id=${encodeURIComponent(demoClient.client_id)}`,
        );
        setBriefing(result);
        pushAssistantMessage({
          text: `Here's your pre-meeting briefing for "${result.event_title}" — ${result.start_time}.`,
          briefing: result,
        });
        return;
      }

      const response = await api<ConversationResponse>("/assistant/respond", {
        method: "POST",
        body: JSON.stringify({
          client_id: demoClient.client_id,
          user_id: DEMO_USER_ID,
          message,
          context,
        }),
      });

      setContext(response.context);
      setPendingProposal(response.proposal ?? null);
      pushAssistantMessage({
        text: response.assistant_message,
        proposal: response.proposal ?? undefined,
        triageResults: response.triage_results?.length ? response.triage_results : undefined,
      });
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "The assistant could not respond.");
    } finally {
      setBusy(false);
    }
  }

  // Called from triage panel "Book this meeting" button
  function handleBookMeetingFromEmail(triage: EmailTriageResult) {
    const senderName = triage.sender.replace(/<.*?>/, "").trim();
    const attendees = triage.proposed_meeting_attendees.join(", ") || senderName;
    const prompt = `Schedule a meeting with ${attendees} for ${triage.proposed_meeting_time} about ${triage.subject}`;
    setInput(prompt);
  }

  // Called from triage panel "Draft reply" button
  function handleDraftReplyFromEmail(triage: EmailTriageResult) {
    const senderName = triage.sender.replace(/<.*?>/, "").trim().split(" ")[0];
    const prompt = `Reply to ${senderName} about: ${triage.subject}`;
    setInput(prompt);
  }

  async function handleApprove() {
    if (!pendingProposal || busy) return;

    setBusy(true);
    setError(null);

    try {
      const action = await api<ActionRecord>("/actions", {
        method: "POST",
        body: JSON.stringify({
          client_id: demoClient.client_id,
          user_id: DEMO_USER_ID,
          action_type: pendingProposal.action_type,
          payload: pendingProposal.payload,
        }),
      });

      let finalAction = action;

      if (action.approval_status === "pending") {
        const approvals = await api<ApprovalRecord[]>(`/approvals?client_id=${encodeURIComponent(demoClient.client_id)}`);
        const matchingApproval = approvals.find(
          (approval) => approval.action_id === action.action_id && approval.status === "pending",
        );

        if (matchingApproval) {
          await api<ApprovalRecord>("/approvals/decision", {
            method: "POST",
            body: JSON.stringify({
              approval_id: matchingApproval.approval_id,
              reviewer_id: DEMO_USER_ID,
              decision: "approved",
            }),
          });
          const actions = await api<ActionRecord[]>(`/actions?client_id=${encodeURIComponent(demoClient.client_id)}`);
          finalAction = actions.find((candidate) => candidate.action_id === action.action_id) ?? action;
        }
      }

      pushAssistantMessage({
        text: formatExecutionSummary(pendingProposal.action_type, finalAction.result),
        result: finalAction.result,
        emphasis: finalAction.result?.error ? "warning" : "success",
      });
      setPendingProposal(null);
      setRevisionProposal(null);
      setContext(null);
    } catch (approvalError) {
      setError(approvalError instanceof Error ? approvalError.message : "Failed to approve draft.");
    } finally {
      setBusy(false);
    }
  }

  function handleRevise() {
    if (!pendingProposal) return;
    setRevisionProposal(pendingProposal);
    setContext(null);
    setPendingProposal(null);
    pushAssistantMessage({
      text: "Tell me what you want changed and I'll revise the draft. Your feedback will be stored as a preference for the next version.",
    });
  }

  async function handleReset() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api<{ status: string }>("/demo/reset", { method: "POST", body: JSON.stringify({}) });
      await api<ClientConfig>("/clients", {
        method: "POST",
        body: JSON.stringify({
          ...demoClient,
          scheduling_preferences: {},
          approval_rules: { email_tone: "concise and warm" },
        }),
      });
      setIntegrations([]);
      setMessages([{
        id: "welcome-reset",
        role: "assistant",
        text: "Fresh start. Tell me what you want handled.",
      }]);
      setContext(null);
      setPendingProposal(null);
      setRevisionProposal(null);
      setBriefing(null);
      setInput("");
      setClientReady(true);
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : "Failed to reset demo.");
    } finally {
      setBusy(false);
    }
  }

  async function handleConnectGoogle() {
    setBusy(true);
    setError(null);
    try {
      const response = await api<{ auth_url: string }>(
        `/integrations/google/start?client_id=${encodeURIComponent(demoClient.client_id)}`,
      );
      window.location.href = response.auth_url;
    } catch (connectError) {
      setError(connectError instanceof Error ? connectError.message : "Failed to start Google connection.");
      setBusy(false);
    }
  }

  async function handleGetBriefing(eventId: string) {
    setBusy(true);
    setError(null);
    try {
      const result = await api<MeetingBriefing>(
        `/briefing?client_id=${encodeURIComponent(demoClient.client_id)}&event_id=${encodeURIComponent(eventId)}`,
      );
      setBriefing(result);
      pushAssistantMessage({
        text: `Here's your pre-meeting briefing for "${result.event_title}".`,
      });
    } catch (briefingError) {
      setError(briefingError instanceof Error ? briefingError.message : "Could not generate briefing.");
    } finally {
      setBusy(false);
    }
  }

  const googleIntegration = integrations.find((i) => i.provider === "google");

  return (
    <main className="chat-shell">
      <section className="chat-frame">
        <header className="chat-hero">
          <div>
            <p className="eyebrow">CEO-Agents</p>
            <h1>Approve work in chat, not in a dashboard.</h1>
            <p className="hero-copy">
              Ask for an inbox triage, email reply, or calendar change. The assistant reads your real data,
              drafts a polished response, and asks if it looks good — nothing is sent without your approval.
            </p>
            <div className="prompt-strip">
              {SUGGESTED_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  className="prompt-chip"
                  type="button"
                  onClick={() => setInput(prompt)}
                  disabled={busy}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
          <div className="hero-meta">
            <span className="status-pill">{clientReady ? "Demo ready" : "Preparing demo"}</span>
            <div className="connector-card">
              <strong>Google connector</strong>
              <span>
                {googleIntegration?.status === "connected"
                  ? `Connected${googleIntegration.connected_account ? ` as ${googleIntegration.connected_account}` : ""}`
                  : "Not connected — inbox & calendar features require this"}
              </span>
              <button className="ghost-button" type="button" onClick={handleConnectGoogle} disabled={busy}>
                Connect Google
              </button>
            </div>
            <div className="connector-card">
              <strong>Focus blocks</strong>
              <span>{demoClient.focus_blocks?.join(", ") || "None configured"}</span>
            </div>
            <button className="ghost-button" type="button" onClick={handleReset} disabled={busy}>
              Reset demo
            </button>
          </div>
        </header>

        <section className="thread">
          {messages.map((message) => (
            <article
              key={message.id}
              className={
                message.role === "user"
                  ? "bubble bubble-user"
                  : message.emphasis === "success"
                    ? "bubble bubble-assistant bubble-success"
                    : message.emphasis === "warning"
                      ? "bubble bubble-assistant bubble-warning"
                      : "bubble bubble-assistant"
              }
            >
              <span className="bubble-label">{message.role === "user" ? "You" : "Assistant"}</span>
              <p style={{ whiteSpace: "pre-wrap" }}>{message.text}</p>

              {/* Triage results panel */}
              {message.triageResults && message.triageResults.length > 0 && (
                <TriagePanel
                  results={message.triageResults}
                  onBookMeeting={handleBookMeetingFromEmail}
                  onDraftReply={handleDraftReplyFromEmail}
                />
              )}

              {/* Meeting briefing panel */}
              {message.briefing && (
                <BriefingPanel
                  briefing={message.briefing}
                  onDraftFollowUp={(prompt) => setInput(prompt)}
                />
              )}

              {/* Draft proposal card */}
              {message.proposal && (
                <div className="proposal-card">
                  <div className="proposal-head">
                    <div>
                      <span className="proposal-kind">{message.proposal.kind === "email" ? "Email Draft" : "Calendar Draft"}</span>
                      <h2>{message.proposal.title}</h2>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-end" }}>
                      <span className="status-pill subtle">
                        {message.proposal.approval_required ? "Awaiting approval" : "Ready to place"}
                      </span>
                      <ConfidenceBadge score={message.proposal.confidence_score} />
                    </div>
                  </div>
                  <p className="proposal-summary">{message.proposal.summary}</p>
                  <div className="proposal-meta">
                    <span className="mini-pill">{message.proposal.confidence_label}</span>
                    <span className="mini-pill">{message.proposal.source}</span>
                  </div>
                  <dl className="proposal-details">
                    {message.proposal.details.map((detail) => (
                      <div key={`${message.id}-${detail.label}`} className="detail-row">
                        <dt>{detail.label}</dt>
                        <dd style={{ whiteSpace: detail.label === "Draft" ? "pre-wrap" : "normal" }}>
                          {detail.value}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  {message.proposal.warnings.length > 0 && (
                    <div className="warning-block">
                      <strong>Before you approve</strong>
                      <ul>
                        {message.proposal.warnings.map((warning) => (
                          <li key={`${message.id}-${warning}`}>{warning}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {/* Execution result */}
              {message.result && !message.result.error && (
                <div className="result-card">
                  <strong>Execution result</strong>
                  {"draft_id" in message.result && (
                    <p>Gmail draft created — Subject: {String(message.result.subject ?? "No subject")}</p>
                  )}
                  {"html_link" in message.result && (
                    <p>
                      Calendar event placed.{" "}
                      <a href={String(message.result.html_link)} target="_blank" rel="noreferrer">
                        Open in Google Calendar
                      </a>
                    </p>
                  )}
                </div>
              )}
            </article>
          ))}

          {/* Briefing panel — shown below thread when available */}
          {briefing && (
            <article className="bubble bubble-assistant">
              <span className="bubble-label">Assistant</span>
              <BriefingPanel briefing={briefing} />
            </article>
          )}

          {busy && <div className="typing-indicator">Assistant is thinking...</div>}

          {pendingProposal && (
            <aside className="approval-bar">
              <div>
                <strong>Does this look good to you?</strong>
                <p>Approve to execute, or revise to make changes. Revision feedback is remembered.</p>
              </div>
              <div className="approval-actions">
                <button className="ghost-button" type="button" onClick={handleRevise} disabled={busy}>
                  Revise
                </button>
                <button className="primary-button" type="button" onClick={handleApprove} disabled={busy}>
                  Looks good
                </button>
              </div>
            </aside>
          )}

          <div ref={threadEndRef} />
        </section>

        <form className="composer" onSubmit={handleSubmit}>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                e.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder="Example: Triage my inbox. Or: Schedule a board call with Sarah next Tuesday."
            rows={3}
            disabled={busy || !clientReady}
          />
          <div className="composer-actions">
            <small>Enter to send · Shift+Enter for new line · All actions require your approval before execution.</small>
            <button className="primary-button" type="submit" disabled={busy || !input.trim() || !clientReady}>
              Send
            </button>
          </div>
        </form>

        {error && <div className="status-banner error">{error}</div>}
      </section>
    </main>
  );
}
