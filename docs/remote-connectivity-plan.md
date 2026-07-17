# Remote Connectivity Plan

Status: deferred future work. Calldex remains loopback-only today.

## Summary

Add an optional Calldex Remote service that lets a user open a hosted dashboard from an arbitrary browser, authenticate, and connect to a Calldex installation running on their computer. The local installation remains the authority for Codex, repository access, Gemini, and LiveKit credentials. It connects outward to a relay; no local ports or Codex protocols are exposed to the public internet.

This is intentionally postponed until the local dashboard and voice workflow are stable.

## Goals

- Work from a normal browser without requiring Tailscale, a VPN, or a browser extension.
- Pair a local Calldex installation with a user account through a short-lived code or QR code.
- Support one-time browser sessions and explicitly trusted, revocable devices.
- Reuse the current thread, timeline, composer, LiveKit call, and voice-selection experiences.
- Keep Codex authentication, repository access, Gemini credentials, and LiveKit API secrets local.
- Require explicit authorization for actions that can modify a repository.
- Provide clear offline, reconnecting, expired-session, and revoked-device states.

## Non-goals

- Exposing the Codex SDK, FastAPI port, or dashboard port directly to the internet.
- Allowing the relay to run Codex or access repositories independently.
- Synchronizing repositories or Codex state into cloud storage.
- Supporting multiple local OS users in the first version.
- Replacing LiveKit's media transport.
- Public anonymous links.

## Proposed Architecture

```text
Hosted React dashboard
        |
        | HTTPS + authenticated encrypted RPC
        v
Calldex relay / control plane
        ^
        | outbound authenticated WebSocket
        |
Local Calldex connector ----> local Codex SDK ----> repositories
        |
        +--------------------> LiveKit Cloud <---- remote browser
```

### Hosted dashboard

- Deploy a hosted version of the existing React UI.
- Authenticate users with passkeys, OAuth, or email magic links.
- Show the user's paired Calldex hosts and their online state.
- Route API requests to the selected host through the relay.
- Connect directly to LiveKit after receiving a short-lived token through the authenticated RPC channel.

### Local connector

- Run alongside the existing API and LiveKit worker.
- Maintain an outbound TLS WebSocket to the relay with exponential-backoff reconnection.
- Generate and protect a long-lived host identity key.
- Verify the account, device, session, and capability on every forwarded request.
- Translate authorized RPC calls into calls to the existing `CodexService`.
- Keep the existing services bound to `127.0.0.1`.
- Report presence and coarse health without uploading thread or repository content.

### Relay and control plane

- Authenticate hosted-dashboard users.
- Maintain account-to-host and account-to-device relationships.
- Match browser sessions with the correct connected host.
- Relay bounded request and response envelopes.
- Rate-limit login, pairing, session creation, and RPC traffic.
- Store revocation state and audit metadata.
- Avoid storing Codex messages, thread contents, repository paths, prompts, or tool results.

The relay should be treated as untrusted for content. Application-layer encryption should prevent it from reading RPC payloads once the browser and local host are paired.

## User Flows

### Claim a local installation

1. The user selects **Enable remote access** in the local dashboard.
2. The local connector creates a pending claim with a short expiry.
3. The dashboard displays a human-readable code and a QR code containing a longer secret and host-key fingerprint.
4. The user signs into the hosted dashboard and enters or scans the code.
5. The local dashboard displays the requesting account and asks for confirmation.
6. The browser and local connector bind their public keys to the approved account/host relationship.
7. The control plane records the claimed host and invalidates the code.

Pairing codes must be single-use, expire within minutes, and be aggressively rate-limited. A short code locates and authorizes a pending exchange; it must not be used directly as an encryption key.

### Use a random or shared browser

1. The user opens the hosted dashboard and signs in.
2. The user selects an online Calldex host.
3. The user chooses **One-time session**.
4. The browser creates an ephemeral key and receives a short-lived capability session.
5. The session expires after inactivity or when the tab is closed or explicitly signed out.
6. No durable device credential is stored.

One-time sessions should default to read and voice access. Sending a Codex message or enabling workspace writes should require a fresh confirmation.

### Trust a browser

1. The user selects **Trust this device** after signing in.
2. The browser generates a non-exportable device key with WebCrypto.
3. The device receives a name, creation time, last-used time, and explicit capabilities.
4. Future sessions prove possession of the device key.
5. The user can revoke the device locally or from the hosted account page.

### Revoke access

- Revoke an individual browser device.
- Revoke every session for an account.
- Unclaim a local host.
- Disable remote connectivity locally.
- Rotate the local host key after compromise.
- Immediately close active relay sessions when relevant revocation state changes.

## Authentication and Authorization

Authentication and pairing are separate:

- Account authentication establishes who the remote user is.
- Host claiming associates a local installation with that account.
- Device pairing establishes a browser-held key and trust level.
- Capability authorization determines what the session may do.

Initial capabilities:

- `threads:list`
- `threads:read`
- `voice:connect`
- `threads:message`
- `workspace:write`
- `devices:manage`

The local connector must enforce capabilities independently rather than trusting only a relay decision. `workspace:write` should never be silently inferred from read access.

## Remote API Shape

Use a small versioned RPC protocol rather than transparently proxying arbitrary local HTTP requests.

Example request envelope:

```json
{
  "version": 1,
  "request_id": "uuid",
  "host_id": "host_uuid",
  "method": "threads.read",
  "params": { "thread_id": "..." },
  "issued_at": "ISO-8601 timestamp",
  "expires_at": "ISO-8601 timestamp"
}
```

Candidate methods:

- `health.get`
- `threads.list`
- `threads.read`
- `threads.message`
- `livekit.token`
- `selection.get`
- `selection.set`

Every envelope should be authenticated, encrypted, bounded in size, assigned a unique request ID, and rejected when expired or replayed. Large responses should be paginated or streamed with explicit limits.

## Voice and LiveKit

- Keep LiveKit as the media plane; do not proxy audio through the Calldex relay.
- Request LiveKit tokens through an authenticated remote RPC method.
- Continue generating unique rooms and participant identities server-side.
- Keep LiveKit API credentials on the local host.
- Make room tokens short-lived and scoped to exactly one room.
- Preserve participant-attribute synchronization for requested and active Codex threads.
- End the LiveKit session when its paired remote session is revoked.

## Content Encryption

Preferred design:

- Each local host has a long-lived identity key.
- Trusted browsers have non-exportable device keys.
- Pairing binds the browser key to the host fingerprint.
- Browser and host derive per-session encryption keys.
- RPC payloads use authenticated encryption with monotonic counters or unique nonces.
- The relay sees routing metadata, sizes, timing, and encrypted envelopes, but not thread content.

Use a reviewed protocol or library. Do not invent a password-based encryption scheme from the short pairing code. If end-to-end encryption is deferred from the first prototype, document that the relay can read content and do not call that prototype production-ready.

## Security Requirements

- TLS for every network connection.
- HTTP-only, Secure, SameSite cookies for hosted account sessions.
- CSRF and strict origin/host validation for browser mutations.
- Single-use, short-lived pairing claims.
- Rate limits and lockouts for pairing-code attempts.
- Replay protection and clock-skew bounds for RPC envelopes.
- Payload, event-count, transcript, and request-duration limits.
- Local enforcement of account, host, device, session, and capability state.
- Explicit confirmation for repository-writing actions from one-time sessions.
- Device and session revocation with immediate connection teardown.
- Secret redaction and no repository content in control-plane logs.
- Security headers and a restrictive content security policy on the hosted UI.
- Dependency and protocol-version upgrade strategy.
- Recovery codes or another documented account-recovery path.

## Data Stored Remotely

Allowed:

- Account identifier and authentication metadata.
- Host identifier, public key, display name, and online timestamp.
- Device identifier, public key, display name, trust mode, and last-used timestamp.
- Capability grants and revocation state.
- Pairing attempt counters and expirations.
- Audit metadata such as actor, method, host, timestamp, and result class.

Not allowed by default:

- Codex authentication material.
- Gemini or LiveKit secrets.
- Repository contents or diffs.
- Thread messages, prompts, transcripts, or raw tool results.
- Absolute repository paths.
- Unredacted request or response bodies.

## Operational Requirements

- Start the local connector automatically only after remote access is enabled.
- Clearly indicate when remote access is active.
- Allow a local emergency-disable action that works without cloud connectivity.
- Handle Mac sleep, network changes, relay restarts, and duplicate connector sessions.
- Show remote clients whether the host is offline, sleeping, busy, or incompatible.
- Version the relay protocol and reject unsupported versions clearly.
- Add metrics for connection count, reconnects, latency, errors, and bounded payload sizes without logging content.
- Define retention periods for pairing, device, session, and audit records.

## Implementation Phases

### Phase 0: Local hardening

- Preserve loopback-only binding.
- Separate read, message, token, and administrative capabilities internally.
- Add request IDs, structured audit events, and payload limits.
- Add an explicit remote-access feature flag that defaults to off.
- Ensure all current local tests remain unchanged when the feature is disabled.

### Phase 1: Relay proof of concept

- Add an outbound local connector.
- Implement host presence and a versioned encrypted echo RPC.
- Build a minimal authenticated hosted page listing online hosts.
- Exercise sleep, reconnect, relay restart, and duplicate-host behavior.
- Do not expose Codex methods yet.

### Phase 2: Claiming and device management

- Implement account login.
- Add code and QR-based host claiming with local confirmation.
- Add one-time browser sessions and trusted-device keys.
- Add device listing, naming, expiry, and revocation.
- Complete abuse and pairing-race tests.

### Phase 3: Read-only Codex access

- Add `threads.list` and `threads.read` RPC methods.
- Reuse the bounded timeline normalization from the local API.
- Add pagination, polling/backoff, offline caching rules, and stale-data states.
- Verify that remote browsing cannot resume or modify a Codex thread.

### Phase 4: Voice access

- Add authenticated `livekit.token` RPC.
- Reuse the existing LiveKit session and participant-attribute synchronization.
- Verify session teardown and token rejection after revocation.
- Measure browser-to-agent latency separately from relay latency.

### Phase 5: Remote messages and workspace writes

- Add `threads.message` behind explicit capabilities.
- Require a confirmation or elevated session for one-time devices.
- Surface the target host, repository, thread, and access level before sending.
- Add idempotency, cancellation, timeout, and reconnect behavior.
- Audit every mutation without recording prompt or response bodies.

### Phase 6: Production hardening

- Complete threat modeling and external security review.
- Add account recovery, key rotation, abuse handling, quotas, and operational alerts.
- Run load, reconnect-storm, large-thread, long-call, and compromised-device exercises.
- Document privacy, retention, deletion, support, and incident-response procedures.

## Test Plan

### Pairing

- Successful code and QR claims.
- Expired, reused, guessed, and concurrently claimed codes.
- Incorrect host fingerprint and relay substitution attempts.
- Local denial and account mismatch.
- Rate limiting and temporary lockout.

### Sessions and authorization

- One-time session expiry and tab-close behavior.
- Trusted-device proof of possession.
- Capability escalation attempts.
- Device, account, and host revocation during active RPC and LiveKit sessions.
- Replay, duplicate request ID, stale timestamp, and invalid signature rejection.

### Connectivity

- Mac sleep and wake.
- Network handoff and transient disconnects.
- Relay restart and reconnect backoff.
- Multiple browser tabs and duplicate connector processes.
- Host offline before and during requests.

### Codex behavior

- Read-only remote browsing never resumes or modifies a thread.
- Large histories remain bounded and do not exhaust browser or relay memory.
- Remote messages target only the confirmed host and thread.
- Workspace writes require the expected capability and confirmation.
- Secrets and thread content never appear in relay logs.

### Acceptance check

1. Enable remote access on a Mac and claim it through a short-lived code.
2. Open the hosted UI in a fresh private browser on another network.
3. Sign in, create a one-time session, and browse recent tasks.
4. Start a LiveKit call, select a task by voice, and confirm synchronized UI selection.
5. Attempt a workspace-writing message and complete the required elevation.
6. Revoke the browser and verify that RPC, polling, and LiveKit access stop immediately.
7. Confirm that the Mac exposes no public listening port and that cloud logs contain no Codex content.

## Decisions to Make Later

- Authentication provider and account-recovery model.
- Relay hosting platform and regional strategy.
- Reviewed end-to-end encryption protocol/library.
- Whether trusted devices are necessary for V1 or one-time sessions are sufficient.
- Default capability set and elevation duration.
- Whether local approval is mandatory for every new trusted browser.
- Audit retention and user-visible activity history.
- Hosted service pricing, quotas, and operational ownership.

## Preconditions Before Starting

- The local dashboard, composer, and LiveKit call remain stable in long-running use.
- Thread discovery and normalization work across supported Codex versions.
- Local APIs have bounded payloads and non-overlapping polling.
- Voice latency and interruption behavior meet the local acceptance target.
- The local service has a clear permission model for read-only and workspace-writing operations.
