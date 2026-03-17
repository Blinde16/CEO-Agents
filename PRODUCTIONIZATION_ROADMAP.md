# Productionization Roadmap

Post-demo work to systemize for client-by-client deployment.

## Phase 1: Persistence (Blocker)
- [ ] PostgreSQL for clients, preferences, action logs, approval history
- [ ] Encrypted token storage for OAuth credentials
- [ ] Session persistence for conversation context across page reloads

## Phase 2: Authentication & Tenant Isolation
- [ ] User login (OAuth2/OIDC or magic link)
- [ ] User → client mapping with RBAC
- [ ] API authentication on every endpoint (JWT or session tokens)
- [ ] Row-level data isolation

## Phase 3: Deployment Infrastructure
- [ ] Dockerfile + docker-compose
- [ ] CI/CD pipeline
- [ ] Environment configs for staging/prod
- [ ] Secrets management (Vault, AWS SSM, etc.)
- [ ] Per-tenant provisioning or shared multi-tenant with isolation

## Phase 4: Frontend Productionization
- [ ] Break page.tsx into proper components (ChatPanel, TriagePanel, ProposalCard, etc.)
- [ ] Auth flow (login/logout)
- [ ] Mobile responsiveness
- [ ] PWA or native wrapper for push notifications
- [ ] Settings/preferences UI for client self-service

## Phase 5: Observability
- [ ] Structured logging
- [ ] Request tracing
- [ ] LLM call auditing
- [ ] Health monitoring and alerting

## Phase 6: Voice Integration
- [ ] Speech-to-text (Whisper API or Deepgram)
- [ ] Text-to-speech for responses (OpenAI TTS, ElevenLabs)
- [ ] Real-time streaming (WebSocket or WebRTC)
- [ ] Voice activity detection / interruption handling

## Phase 7: Tasks & Reminders
- [ ] Real implementation (Google Tasks, local scheduler, or push notifications)
- [ ] "Remind me to follow up with Sarah on Friday" must actually fire

## Phase 8: Microsoft 365 Support
- [ ] Outlook email integration
- [ ] Outlook calendar integration
- [ ] Doubles addressable market

## Phase 9: Rate Limiting & Cost Controls
- [ ] Per-client rate limits
- [ ] Token budgets per client
- [ ] Cost tracking and reporting
