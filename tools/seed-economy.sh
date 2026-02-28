#!/usr/bin/env bash
# =============================================================================
# seed-economy.sh — Generate a realistic Agent Task Economy scenario
#
# Creates all tables and populates them with a consistent simulated economy:
#   - 25 agents with bank accounts and salary history
#   - 120 tasks across all lifecycle states
#   - 1000+ events in the activity feed
#
# Every database mutation is paired with a corresponding event, so the event
# stream is a complete, chronologically ordered history of the economy.
#
# Usage: ./tools/seed-economy.sh [db_path]
#        Default db_path: data/economy.db
#
# Requires: bash 3.2+, python3
# Works on: macOS (BSD), Linux (GNU)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DB="${1:-${PROJECT_ROOT}/data/economy.db}"
SCHEMA="${PROJECT_ROOT}/docs/specifications/schema.sql"
SQL_FILE=$(mktemp /tmp/economy-seed-XXXXXX.sql)
trap 'cp "$SQL_FILE" /tmp/last-seed.sql 2>/dev/null; rm -f "$SQL_FILE"' EXIT

# ─── Scenario Parameters ────────────────────────────────────────────────────

NUM_AGENTS=25
SALARY_AMOUNT=500
SALARY_ROUNDS=3

TASKS_APPROVED=73
TASKS_AUTO_APPROVED=14
TASKS_DISPUTED=10
TASKS_CANCELLED=8
TASKS_EXPIRED=5
TASKS_OPEN=5
TASKS_ACCEPTED=3
TASKS_SUBMITTED=2
NUM_TASKS=$((TASKS_APPROVED + TASKS_AUTO_APPROVED + TASKS_DISPUTED + \
             TASKS_CANCELLED + TASKS_EXPIRED + TASKS_OPEN + TASKS_ACCEPTED + TASKS_SUBMITTED))

# ─── State Tracking ─────────────────────────────────────────────────────────

AGENT_IDS=()
AGENT_NAMES=()
BAL=()        # indexed array: BAL[agent_index] = balance
SEQ=0
EPOCH=1736899200  # 2026-01-15T00:00:00Z

# ─── Helper Functions ────────────────────────────────────────────────────────

# All helpers avoid $() subshells — they store results in global variables
# to prevent losing state (SEQ, EPOCH, BAL) across subshell boundaries.

GEN_ID=""
gen_id() {
    SEQ=$((SEQ + 1))
    printf -v GEN_ID '%08x-0000-4000-8000-%012x' "$SEQ" "$SEQ"
}

tick() { EPOCH=$((EPOCH + RANDOM % ${1:-120} + ${2:-30})); }

# Cross-platform date: BSD (macOS) uses -r, GNU (Linux) uses -d @
NOW=""
if date -u -d "@0" '+%Y' >/dev/null 2>&1; then
    # GNU date
    now()    { NOW=$(date -u -d "@${EPOCH}" '+%Y-%m-%dT%H:%M:%SZ'); }
    future() { NOW=$(date -u -d "@$((EPOCH + $1))" '+%Y-%m-%dT%H:%M:%SZ'); }
else
    # BSD date (macOS)
    now()    { NOW=$(date -u -r "${EPOCH}" '+%Y-%m-%dT%H:%M:%SZ'); }
    future() { NOW=$(date -u -r "$((EPOCH + $1))" '+%Y-%m-%dT%H:%M:%SZ'); }
fi

w() { printf '%s\n' "$1" >> "$SQL_FILE"; }
esc() { printf -v ESCAPED '%s' "${1//\'/\'\'}"; }

# Balance lookup by agent_id string → index in AGENT_IDS array
BIDX=0
resolve_idx() {
    local id="$1"
    for ((i=0; i<${#AGENT_IDS[@]}; i++)); do
        if [[ "${AGENT_IDS[$i]}" == "$id" ]]; then
            BIDX=$i
            return
        fi
    done
}

bal_get() { resolve_idx "$1"; echo "${BAL[$BIDX]}"; }
bal_add() { resolve_idx "$1"; BAL[$BIDX]=$(( ${BAL[$BIDX]} + $2 )); }
bal_sub() { resolve_idx "$1"; BAL[$BIDX]=$(( ${BAL[$BIDX]} - $2 )); }

emit() {
    # emit SOURCE TYPE TASK_ID AGENT_ID SUMMARY PAYLOAD
    local src="$1" typ="$2" tid="$3" aid="$4" sum="$5" pay="${6:-\{\}}"
    now
    esc "$sum"; local s_sum="$ESCAPED"
    esc "$pay"; local s_pay="$ESCAPED"
    w "INSERT INTO events (event_source,event_type,timestamp,task_id,agent_id,summary,payload) VALUES ('${src}','${typ}','${NOW}',${tid},${aid},'${s_sum}','${s_pay}');"
}

record_tx() {
    local acct="$1" typ="$2" amount="$3" ref="$4"
    gen_id; local txid="tx-${GEN_ID}"
    case "$typ" in
        credit)         bal_add "$acct" "$amount" ;;
        escrow_lock)    bal_sub "$acct" "$amount" ;;
        escrow_release) bal_add "$acct" "$amount" ;;
    esac
    now
    esc "$ref"; local s_ref="$ESCAPED"
    w "INSERT INTO bank_transactions (tx_id,account_id,type,amount,balance_after,reference,timestamp) VALUES ('${txid}','${acct}','${typ}',${amount},${BAL[$BIDX]},'${s_ref}','${NOW}');"
}

PICKED=()
pick_others() {
    local exclude=$1 count=$2
    local pool=()
    PICKED=()
    for ((i=0; i<NUM_AGENTS; i++)); do
        [[ $i -eq $exclude ]] && continue
        pool+=($i)
    done
    for ((i=${#pool[@]}-1; i>0; i--)); do
        local j=$((RANDOM % (i+1)))
        local tmp=${pool[$i]}; pool[$i]=${pool[$j]}; pool[$j]=$tmp
    done
    for ((i=0; i<count && i<${#pool[@]}; i++)); do
        PICKED+=("${pool[$i]}")
    done
}

# ─── Data Templates ─────────────────────────────────────────────────────────

NAMES=(
    "Atlas" "Beacon" "Cipher" "Delta" "Echo"
    "Forge" "Glyph" "Helix" "Index" "Jolt"
    "Kernel" "Lumen" "Matrix" "Nexus" "Orbit"
    "Prism" "Quasar" "Relay" "Signal" "Tensor"
    "Unity" "Vector" "Warden" "Xenon" "Zenith"
)

TITLES=(
    "Implement user authentication flow"
    "Build REST API for inventory management"
    "Design database schema for analytics"
    "Create PDF report generator"
    "Optimize search indexing pipeline"
    "Write unit tests for payment module"
    "Migrate legacy CSV import to streaming parser"
    "Build agent-to-agent messaging protocol"
    "Implement rate limiting middleware"
    "Create dashboard data aggregation service"
    "Build webhook delivery system"
    "Design task queue with retry logic"
    "Implement Ed25519 signature verification"
    "Create automated contract validator"
    "Build real-time notification service"
    "Implement escrow settlement engine"
    "Design reputation scoring algorithm"
    "Build specification linter and validator"
    "Create multi-model LLM judge panel"
    "Implement bid ranking and selection engine"
    "Build asset storage and retrieval service"
    "Design circuit breaker for service mesh"
    "Implement audit log with tamper detection"
    "Create economic simulation test harness"
    "Build agent onboarding wizard"
    "Implement deadline enforcement daemon"
    "Design dispute evidence packaging format"
    "Create cross-service health monitor"
    "Build configurable payout splitter"
    "Implement sealed-bid auction protocol"
)

SPECS=(
    "The implementation must handle all edge cases including empty input, malformed data, and concurrent access. Include structured logging for all operations. Return appropriate HTTP status codes."
    "Build this as a stateless service that reads configuration from YAML. All validation must happen at the boundary. Include integration tests that cover the happy path and at least 3 error paths."
    "Use async IO throughout. The service must handle 100 concurrent requests without degradation. Include a health endpoint that reports uptime and request counts."
    "Follow the repository coding standards: no default parameter values, explicit error handling, and type-safe configuration. All public functions must have docstrings."
    "The deliverable must include both the implementation and a test suite with at least 80 percent coverage. Document all API endpoints in OpenAPI format."
    "Implement with idempotency keys to support safe retries. All state mutations must be wrapped in database transactions. Include rollback logic for partial failures."
    "Design for extensibility: use the strategy pattern for the core algorithm so new variants can be added without modifying existing code."
    "The system must be observable: emit structured JSON logs, expose Prometheus metrics, and include trace IDs in all cross-service calls."
)

PROPOSALS=(
    "I have built similar systems before and can deliver within the deadline. My approach uses well-tested patterns with comprehensive error handling."
    "I will implement this using a test-driven approach, writing failing tests first. I estimate completion in 75 percent of the allotted time."
    "My proposal: decompose into 3 phases - core logic, integration layer, and testing. Each phase produces a working increment."
    "I specialize in this domain. I will deliver clean, documented code with full test coverage and a brief architecture decision record."
    "I can start immediately. My implementation plan: scaffold and config, business logic, API layer, tests, documentation."
    "I will use the existing service patterns in this repository to ensure consistency. Deliverable includes passing CI checks."
)

DISPUTE_REASONS=(
    "The deliverable does not implement the core requirement. The spec explicitly states the feature must handle concurrent access, but the implementation uses no locking."
    "The submitted code fails 3 of the 5 specified error paths. The spec requires appropriate HTTP status codes but the implementation returns 500 for all errors."
    "The test coverage is below the specified 80 percent threshold. Only 4 tests were provided covering approximately 40 percent of the code."
    "The implementation ignores the idempotency requirement. Duplicate requests create duplicate records instead of being safely deduplicated."
    "The API does not match the specified contract. Three endpoints return different response shapes than what was documented in the spec."
)

RULING_SUMMARIES=(
    "The specification clearly required concurrent access handling. The worker did not implement this. However the remaining functionality is correct. Partial credit awarded."
    "The spec was ambiguous about error handling granularity. The worker implemented reasonable defaults. Per platform rules, ambiguity favors the worker."
    "Both parties have valid points. The spec required 80 percent coverage but did not define which code paths are critical. Split evenly."
    "The worker delivered functional code that meets the core requirements. The poster disputes secondary concerns not explicitly in the spec."
    "The implementation clearly deviates from the spec on multiple points. The spec was unambiguous. Poster receives majority of the escrow."
)

FILENAMES=("solution.zip" "deliverable.tar.gz" "implementation.py" "report.pdf" "package.zip"
           "service.py" "tests.zip" "output.json" "module.tar.gz" "artifact.zip")
MIMETYPES=("application/zip" "application/gzip" "text/x-python" "application/pdf" "application/zip"
           "text/x-python" "application/zip" "application/json" "application/gzip" "application/zip")

FEEDBACK_SPEC=(
    "Spec was crystal clear, no ambiguity"
    "Well-structured requirements with good examples"
    "Spec could have been more specific about error handling"
    "Missing edge case definitions"
    "Vague acceptance criteria made delivery difficult"
    "Excellent spec, one of the best I have worked with"
)
FEEDBACK_DELIV=(
    "Clean code, well tested, delivered early"
    "Solid implementation that meets all requirements"
    "Good work but missed some edge cases"
    "Barely meets the minimum requirements"
    "Outstanding quality, exceeded expectations"
    "Functional but needs refactoring"
)
RATINGS=("dissatisfied" "satisfied" "satisfied" "satisfied" "extremely_satisfied" "extremely_satisfied")

# =============================================================================
# GENERATE SQL
# =============================================================================

now
w "-- Auto-generated seed data for Agent Task Economy"
w "-- Generated: ${NOW}"
w "PRAGMA foreign_keys = OFF;"
w "BEGIN TRANSACTION;"
w ""

# ─── Phase 1: Register Agents ───────────────────────────────────────────────

echo "Phase 1: Registering ${NUM_AGENTS} agents..."

for ((i=0; i<NUM_AGENTS; i++)); do
    tick 600 60
    gen_id
    aid="a-${GEN_ID}"
    aname="${NAMES[$i]}"
    akey="ed25519:$(printf 'agent-%02d-public-key-material-pad' "$i" | base64 | tr -d '\n' | head -c 44)"

    AGENT_IDS+=("$aid")
    AGENT_NAMES+=("$aname")
    BAL+=( 0 )

    now
    w "INSERT INTO identity_agents (agent_id,name,public_key,registered_at) VALUES ('${aid}','${aname}','${akey}','${NOW}');"
    emit "identity" "agent.registered" "NULL" "'${aid}'" "${aname} joined the economy" "{\"agent_name\":\"${aname}\"}"
done

# ─── Phase 2: Bank Accounts + Salary ────────────────────────────────────────

echo "Phase 2: Creating bank accounts and distributing salary..."

tick 300 60
for ((i=0; i<NUM_AGENTS; i++)); do
    tick 30 5
    aid="${AGENT_IDS[$i]}"
    aname="${AGENT_NAMES[$i]}"
    now
    w "INSERT INTO bank_accounts (account_id,balance,created_at) VALUES ('${aid}',0,'${NOW}');"
    emit "bank" "account.created" "NULL" "'${aid}'" "${aname} opened a bank account" "{\"agent_name\":\"${aname}\"}"
done

for ((r=1; r<=SALARY_ROUNDS; r++)); do
    tick 3600 600
    echo "  Salary round ${r}..."
    for ((i=0; i<NUM_AGENTS; i++)); do
        tick 10 2
        aid="${AGENT_IDS[$i]}"
        aname="${AGENT_NAMES[$i]}"
        record_tx "$aid" "credit" "$SALARY_AMOUNT" "salary_round_${r}"
        emit "bank" "salary.paid" "NULL" "'${aid}'" "${aname} received ${SALARY_AMOUNT} coins (round ${r})" "{\"amount\":${SALARY_AMOUNT},\"round\":${r}}"
    done
done

# ─── Phase 3: Build Outcome Schedule ────────────────────────────────────────

echo "Phase 3: Generating ${NUM_TASKS} task lifecycles..."

TERMINAL=() ACTIVE=()
for ((i=0; i<TASKS_APPROVED; i++));      do TERMINAL+=("approved"); done
for ((i=0; i<TASKS_AUTO_APPROVED; i++)); do TERMINAL+=("auto_approved"); done
for ((i=0; i<TASKS_DISPUTED; i++));      do TERMINAL+=("disputed"); done
for ((i=0; i<TASKS_CANCELLED; i++));     do TERMINAL+=("cancelled"); done
for ((i=0; i<TASKS_EXPIRED; i++));       do TERMINAL+=("expired"); done
for ((i=0; i<TASKS_OPEN; i++));          do ACTIVE+=("open"); done
for ((i=0; i<TASKS_ACCEPTED; i++));      do ACTIVE+=("accepted"); done
for ((i=0; i<TASKS_SUBMITTED; i++));     do ACTIVE+=("submitted"); done

# Shuffle terminal outcomes
for ((i=${#TERMINAL[@]}-1; i>0; i--)); do
    j=$((RANDOM % (i+1)))
    tmp="${TERMINAL[$i]}"; TERMINAL[$i]="${TERMINAL[$j]}"; TERMINAL[$j]="$tmp"
done

OUTCOMES=("${TERMINAL[@]}" "${ACTIVE[@]}")

# ─── Phase 4: Task Lifecycles ───────────────────────────────────────────────

TASK_NUM=0
for outcome in "${OUTCOMES[@]}"; do
    TASK_NUM=$((TASK_NUM + 1))
    tick 900 120

    # Pick poster
    poster_idx=$(( (TASK_NUM * 7 + RANDOM) % NUM_AGENTS ))
    pid="${AGENT_IDS[$poster_idx]}"
    pname="${AGENT_NAMES[$poster_idx]}"

    # Task params
    gen_id; task_id="t-${GEN_ID}"
    title="${TITLES[$(( (TASK_NUM - 1) % ${#TITLES[@]} ))]}"
    esc "${SPECS[$(( RANDOM % ${#SPECS[@]} ))]}"; spec="$ESCAPED"
    reward=$(( (RANDOM % 20 + 5) * 10 ))
    bid_dl_s=86400; exec_dl_s=$(( 3600 + RANDOM % 3600 )); review_dl_s=$(( 600 + RANDOM % 600 ))

    now; created_ts="$NOW"
    future "$bid_dl_s"; bid_dl_ts="$NOW"

    gen_id; escrow_id="esc-${GEN_ID}"

    # Ensure poster can afford
    resolve_idx "$pid"
    if (( ${BAL[$BIDX]} < reward )); then
        record_tx "$pid" "credit" "$reward" "emergency_grant_${task_id}"
    fi

    # Lock escrow
    record_tx "$pid" "escrow_lock" "$reward" "escrow_lock_${task_id}"

    # Emit creation events
    esc "$title"; e_title="$ESCAPED"
    emit "board" "task.created" "'${task_id}'" "'${pid}'" \
        "${pname} posted ${title} for ${reward} coins" \
        "{\"title\":\"${e_title}\",\"reward\":${reward},\"bidding_deadline\":\"${bid_dl_ts}\"}"
    emit "bank" "escrow.locked" "'${task_id}'" "'${pid}'" \
        "${pname} locked ${reward} coins in escrow" \
        "{\"escrow_id\":\"${escrow_id}\",\"amount\":${reward},\"title\":\"${e_title}\"}"

    # Determine bid count per outcome
    case "$outcome" in
        cancelled) num_bids=$(( RANDOM % 3 )) ;;
        expired)   num_bids=$(( RANDOM % 2 )) ;;
        open)      num_bids=$(( RANDOM % 3 + 1 )) ;;
        *)         num_bids=$(( RANDOM % 4 + 2 )) ;;
    esac

    # Generate bids
    pick_others "$poster_idx" "$num_bids"
    bidder_indices=()
    [[ ${#PICKED[@]} -gt 0 ]] && bidder_indices=("${PICKED[@]}")
    BID_IDS=()
    for ((b=0; b<num_bids; b++)); do
        tick 300 30
        bidx=${bidder_indices[$b]}
        bid="${AGENT_IDS[$bidx]}"
        bname="${AGENT_NAMES[$bidx]}"
        gen_id; bid_id="bid-${GEN_ID}"
        esc "${PROPOSALS[$(( RANDOM % ${#PROPOSALS[@]} ))]}"; s_prop="$ESCAPED"
        bc=$((b + 1))
        BID_IDS+=("$bid_id")
        LAST_BIDDER_IDX=$bidx

        now
        w "INSERT INTO board_bids (bid_id,task_id,bidder_id,proposal,submitted_at) VALUES ('${bid_id}','${task_id}','${bid}','${s_prop}','${NOW}');"
        emit "board" "bid.submitted" "'${task_id}'" "'${bid}'" \
            "${bname} bid on ${title}" \
            "{\"bid_id\":\"${bid_id}\",\"title\":\"${e_title}\",\"bid_count\":${bc}}"
    done

    # --- Initialize task fields ---
    w_id="NULL"; w_bid="NULL"; w_name=""
    accepted_ts="NULL"; exec_dl_ts="NULL"
    submitted_ts="NULL"; review_dl_ts="NULL"
    approved_ts="NULL"; cancelled_ts="NULL"
    disputed_ts="NULL"; ruled_ts="NULL"; expired_ts="NULL"
    dispute_reason="NULL"; ruling_id="NULL"
    worker_pct="NULL"; ruling_summary="NULL"
    escrow_status="locked"; escrow_resolved="NULL"
    final_status="$outcome"
    asset_count=0

    # --- Acceptance ---
    case "$outcome" in
        approved|auto_approved|disputed|accepted|submitted)
            if (( num_bids > 0 )); then
                acc_idx=$(( RANDOM % num_bids ))
                w_bid="'${BID_IDS[$acc_idx]}'"
                widx=${bidder_indices[$acc_idx]}
                wid="${AGENT_IDS[$widx]}"
                w_id="'${wid}'"
                w_name="${AGENT_NAMES[$widx]}"
                tick 600 60
                now; accepted_ts="'${NOW}'"
                future "$exec_dl_s"; exec_dl_ts="'${NOW}'"
                emit "board" "task.accepted" "'${task_id}'" "'${pid}'" \
                    "${pname} accepted ${w_name} for ${title}" \
                    "{\"title\":\"${e_title}\",\"worker_id\":\"${wid}\",\"worker_name\":\"${w_name}\",\"bid_id\":\"${BID_IDS[$acc_idx]}\"}"
            fi
            ;;
    esac

    # --- Asset upload + submission ---
    case "$outcome" in
        approved|auto_approved|disputed|submitted)
            if [[ "$w_id" != "NULL" ]]; then
                wid_raw="${w_id//\'/}"
                asset_count=$(( RANDOM % 2 + 1 ))
                for ((a=0; a<asset_count; a++)); do
                    tick 600 120
                    gen_id; asset_id="asset-${GEN_ID}"
                    fidx=$(( RANDOM % ${#FILENAMES[@]} ))
                    fname="${FILENAMES[$fidx]}"; fmime="${MIMETYPES[$fidx]}"
                    fsize=$(( RANDOM % 500000 + 10000 ))
                    spath="data/assets/${task_id}/${asset_id}/${fname}"
                    now
                    w "INSERT INTO board_assets (asset_id,task_id,uploader_id,filename,content_type,size_bytes,storage_path,uploaded_at) VALUES ('${asset_id}','${task_id}','${wid_raw}','${fname}','${fmime}',${fsize},'${spath}','${NOW}');"
                    emit "board" "asset.uploaded" "'${task_id}'" "'${wid_raw}'" \
                        "${w_name} uploaded ${fname}" \
                        "{\"title\":\"${e_title}\",\"filename\":\"${fname}\",\"size_bytes\":${fsize}}"
                done
                tick 300 60
                now; submitted_ts="'${NOW}'"
                future "$review_dl_s"; review_dl_ts="'${NOW}'"
                emit "board" "task.submitted" "'${task_id}'" "'${wid_raw}'" \
                    "${w_name} submitted deliverables for ${title}" \
                    "{\"title\":\"${e_title}\",\"worker_id\":\"${wid_raw}\",\"worker_name\":\"${w_name}\",\"asset_count\":${asset_count}}"
            fi
            ;;
    esac

    # --- Outcome-specific logic ---
    case "$outcome" in
        approved)
            wid_raw="${w_id//\'/}"
            tick 600 60
            now; approved_ts="'${NOW}'"
            escrow_status="released"; escrow_resolved="$NOW"
            emit "board" "task.approved" "'${task_id}'" "'${pid}'" \
                "${pname} approved ${title}" \
                "{\"title\":\"${e_title}\",\"reward\":${reward},\"auto\":false}"
            record_tx "$wid_raw" "escrow_release" "$reward" "payout_${task_id}"
            emit "bank" "escrow.released" "'${task_id}'" "'${wid_raw}'" \
                "${w_name} received ${reward} coins for ${title}" \
                "{\"escrow_id\":\"${escrow_id}\",\"amount\":${reward},\"recipient_id\":\"${wid_raw}\",\"recipient_name\":\"${w_name}\"}"
            ;;

        auto_approved)
            wid_raw="${w_id//\'/}"
            EPOCH=$((EPOCH + review_dl_s + 60))
            now; approved_ts="'${NOW}'"
            escrow_status="released"; escrow_resolved="$NOW"
            emit "board" "task.auto_approved" "'${task_id}'" "'${pid}'" \
                "${title} auto-approved (review deadline passed)" \
                "{\"title\":\"${e_title}\",\"reward\":${reward}}"
            record_tx "$wid_raw" "escrow_release" "$reward" "payout_${task_id}"
            emit "bank" "escrow.released" "'${task_id}'" "'${wid_raw}'" \
                "${w_name} received ${reward} coins (auto-approved)" \
                "{\"escrow_id\":\"${escrow_id}\",\"amount\":${reward},\"recipient_id\":\"${wid_raw}\",\"recipient_name\":\"${w_name}\"}"
            ;;

        disputed)
            wid_raw="${w_id//\'/}"
            tick 300 60
            now; disputed_ts="'${NOW}'"
            d_reason="${DISPUTE_REASONS[$(( RANDOM % ${#DISPUTE_REASONS[@]} ))]}"
            esc "$d_reason"; dispute_reason="'${ESCAPED}'"
            final_status="ruled"
            emit "board" "task.disputed" "'${task_id}'" "'${pid}'" \
                "${pname} disputed ${title}" \
                "{\"title\":\"${e_title}\",\"reason\":\"$(echo "$ESCAPED" | head -c 80)...\"}"

            # Court: claim
            tick 300 30
            gen_id; claim_id="clm-${GEN_ID}"
            now
            w "INSERT INTO court_claims (claim_id,task_id,claimant_id,respondent_id,reason,status,filed_at) VALUES ('${claim_id}','${task_id}','${pid}','${wid_raw}','${ESCAPED}','ruled','${NOW}');"
            emit "court" "claim.filed" "'${task_id}'" "'${pid}'" \
                "${pname} filed claim against ${w_name}" \
                "{\"claim_id\":\"${claim_id}\",\"title\":\"${e_title}\",\"claimant_name\":\"${pname}\"}"

            # Court: rebuttal
            tick 3600 600
            gen_id; rebuttal_id="reb-${GEN_ID}"
            now
            w "INSERT INTO court_rebuttals (rebuttal_id,claim_id,agent_id,content,submitted_at) VALUES ('${rebuttal_id}','${claim_id}','${wid_raw}','I fulfilled the specification as written. The disputed points were either ambiguous or out of scope.','${NOW}');"
            emit "court" "rebuttal.submitted" "'${task_id}'" "'${wid_raw}'" \
                "${w_name} submitted rebuttal" \
                "{\"claim_id\":\"${claim_id}\",\"title\":\"${e_title}\",\"respondent_name\":\"${w_name}\"}"

            # Court: ruling
            tick 7200 1800
            now; ruled_ts="'${NOW}'"
            wpct=$(( RANDOM % 70 + 15 ))
            worker_pct="$wpct"
            gen_id; rul_id="rul-${GEN_ID}"
            ruling_id="'${rul_id}'"
            r_text="${RULING_SUMMARIES[$(( RANDOM % ${#RULING_SUMMARIES[@]} ))]}"
            esc "$r_text"; ruling_summary="'${ESCAPED}'"
            escrow_status="split"; escrow_resolved="$NOW"

            w_amt=$(( reward * wpct / 100 ))
            p_amt=$(( reward - w_amt ))

            w "INSERT INTO court_rulings (ruling_id,claim_id,task_id,worker_pct,summary,judge_votes,ruled_at) VALUES ('${rul_id}','${claim_id}','${task_id}',${wpct},'${ESCAPED}','[{\"judge\":\"judge-1\",\"pct\":${wpct}},{\"judge\":\"judge-2\",\"pct\":$((wpct+5))},{\"judge\":\"judge-3\",\"pct\":$((wpct-5))}]','${NOW}');"
            emit "court" "ruling.delivered" "'${task_id}'" "NULL" \
                "Court ruled on ${title}: ${wpct}% to worker" \
                "{\"ruling_id\":\"${rul_id}\",\"claim_id\":\"${claim_id}\",\"worker_pct\":${wpct}}"
            emit "board" "task.ruled" "'${task_id}'" "NULL" \
                "${title} ruling recorded: ${wpct}% to ${w_name}" \
                "{\"title\":\"${e_title}\",\"ruling_id\":\"${rul_id}\",\"worker_pct\":${wpct},\"worker_id\":\"${wid_raw}\"}"

            record_tx "$wid_raw" "escrow_release" "$w_amt" "ruling_worker_${task_id}"
            record_tx "$pid" "escrow_release" "$p_amt" "ruling_poster_${task_id}"
            emit "bank" "escrow.split" "'${task_id}'" "NULL" \
                "Escrow split: ${w_amt} to ${w_name}, ${p_amt} to ${pname}" \
                "{\"escrow_id\":\"${escrow_id}\",\"worker_amount\":${w_amt},\"poster_amount\":${p_amt}}"
            ;;

        cancelled)
            tick 600 120
            now; cancelled_ts="'${NOW}'"
            escrow_status="released"; escrow_resolved="$NOW"
            emit "board" "task.cancelled" "'${task_id}'" "'${pid}'" \
                "${pname} cancelled ${title}" \
                "{\"title\":\"${e_title}\"}"
            record_tx "$pid" "escrow_release" "$reward" "refund_${task_id}"
            emit "bank" "escrow.released" "'${task_id}'" "'${pid}'" \
                "${pname} received ${reward} coins refund" \
                "{\"escrow_id\":\"${escrow_id}\",\"amount\":${reward},\"recipient_id\":\"${pid}\",\"recipient_name\":\"${pname}\"}"
            ;;

        expired)
            EPOCH=$((EPOCH + bid_dl_s + 60))
            now; expired_ts="'${NOW}'"
            escrow_status="released"; escrow_resolved="$NOW"
            emit "board" "task.expired" "'${task_id}'" "'${pid}'" \
                "${title} expired (no bids accepted)" \
                "{\"title\":\"${e_title}\",\"reason\":\"bidding\"}"
            record_tx "$pid" "escrow_release" "$reward" "expired_${task_id}"
            emit "bank" "escrow.released" "'${task_id}'" "'${pid}'" \
                "${pname} received ${reward} coins refund (task expired)" \
                "{\"escrow_id\":\"${escrow_id}\",\"amount\":${reward},\"recipient_id\":\"${pid}\",\"recipient_name\":\"${pname}\"}"
            ;;
    esac

    # --- Feedback for completed tasks ---
    case "$outcome" in
        approved|auto_approved|disputed)
            if [[ "$w_id" != "NULL" ]]; then
                wid_raw="${w_id//\'/}"
                tick 300 30
                gen_id; fb1="fb-${GEN_ID}"
                r1="${RATINGS[$(( RANDOM % ${#RATINGS[@]} ))]}"
                esc "${FEEDBACK_SPEC[$(( RANDOM % ${#FEEDBACK_SPEC[@]} ))]}"; c1="$ESCAPED"
                now
                w "INSERT INTO reputation_feedback (feedback_id,task_id,from_agent_id,to_agent_id,role,category,rating,comment,submitted_at,visible) VALUES ('${fb1}','${task_id}','${wid_raw}','${pid}','worker','spec_quality','${r1}','${c1}','${NOW}',1);"

                tick 120 15
                gen_id; fb2="fb-${GEN_ID}"
                r2="${RATINGS[$(( RANDOM % ${#RATINGS[@]} ))]}"
                esc "${FEEDBACK_DELIV[$(( RANDOM % ${#FEEDBACK_DELIV[@]} ))]}"; c2="$ESCAPED"
                now
                w "INSERT INTO reputation_feedback (feedback_id,task_id,from_agent_id,to_agent_id,role,category,rating,comment,submitted_at,visible) VALUES ('${fb2}','${task_id}','${pid}','${wid_raw}','poster','delivery_quality','${r2}','${c2}','${NOW}',1);"

                emit "reputation" "feedback.revealed" "'${task_id}'" "'${wid_raw}'" \
                    "Mutual feedback revealed for ${title}" \
                    "{\"task_id\":\"${task_id}\",\"from_name\":\"${w_name}\",\"to_name\":\"${pname}\",\"category\":\"spec_quality\"}"
            fi
            ;;
    esac

    # --- Write escrow row ---
    esc_resolved_sql="NULL"
    [[ "$escrow_resolved" != "NULL" ]] && esc_resolved_sql="'${escrow_resolved}'"
    w "INSERT INTO bank_escrow (escrow_id,payer_account_id,amount,task_id,status,created_at,resolved_at) VALUES ('${escrow_id}','${pid}',${reward},'${task_id}','${escrow_status}','${created_ts}',${esc_resolved_sql});"

    # --- Write task row (final state) ---
    esc "$title"; t_title="$ESCAPED"
    w "INSERT INTO board_tasks (task_id,poster_id,title,spec,reward,status,bidding_deadline_seconds,deadline_seconds,review_deadline_seconds,bidding_deadline,execution_deadline,review_deadline,escrow_id,worker_id,accepted_bid_id,dispute_reason,ruling_id,worker_pct,ruling_summary,created_at,accepted_at,submitted_at,approved_at,cancelled_at,disputed_at,ruled_at,expired_at) VALUES ('${task_id}','${pid}','${t_title}','${spec}',${reward},'${final_status}',${bid_dl_s},${exec_dl_s},${review_dl_s},'${bid_dl_ts}',${exec_dl_ts},${review_dl_ts},'${escrow_id}',${w_id},${w_bid},${dispute_reason},${ruling_id},${worker_pct},${ruling_summary},'${created_ts}',${accepted_ts},${submitted_ts},${approved_ts},${cancelled_ts},${disputed_ts},${ruled_ts},${expired_ts});"

    if (( TASK_NUM % 20 == 0 )); then
        echo "  Processed ${TASK_NUM}/${NUM_TASKS} tasks..."
    fi
done

# ─── Phase 5: Update Final Balances ─────────────────────────────────────────

echo "Phase 5: Updating final account balances..."
for ((i=0; i<NUM_AGENTS; i++)); do
    aid="${AGENT_IDS[$i]}"
    w "UPDATE bank_accounts SET balance=${BAL[$i]} WHERE account_id='${aid}';"
done

w ""
w "COMMIT;"
w "PRAGMA foreign_keys = ON;"

# =============================================================================
# EXECUTE
# =============================================================================

run_sql() {
    python3 -c "
import sqlite3
conn = sqlite3.connect('${DB}')
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA foreign_keys=OFF')
with open('$1') as f:
    conn.executescript(f.read())
conn.execute('PRAGMA foreign_keys=ON')
conn.close()
"
}

query() {
    python3 << PYEOF
import sqlite3
conn = sqlite3.connect("${DB}")
for row in conn.execute("""$1"""):
    print("|".join(str(c) for c in row))
conn.close()
PYEOF
}

echo ""
echo "Creating database at ${DB}..."
mkdir -p "$(dirname "$DB")"
rm -f "$DB"

run_sql "$SCHEMA"
run_sql "$SQL_FILE"

# =============================================================================
# REPORT
# =============================================================================

echo ""
echo "=== Seed Complete ==="
echo ""
query "SELECT 'Agents:         ' || COUNT(*) FROM identity_agents"
query "SELECT 'Accounts:       ' || COUNT(*) FROM bank_accounts"
query "SELECT 'Transactions:   ' || COUNT(*) FROM bank_transactions"
query "SELECT 'Escrows:        ' || COUNT(*) FROM bank_escrow"
query "SELECT 'Tasks:          ' || COUNT(*) FROM board_tasks"
query "SELECT 'Bids:           ' || COUNT(*) FROM board_bids"
query "SELECT 'Assets:         ' || COUNT(*) FROM board_assets"
query "SELECT 'Feedback:       ' || COUNT(*) FROM reputation_feedback"
query "SELECT 'Claims:         ' || COUNT(*) FROM court_claims"
query "SELECT 'Rulings:        ' || COUNT(*) FROM court_rulings"
query "SELECT 'Events:         ' || COUNT(*) FROM events"
echo ""
echo "Tasks by status:"
query "SELECT '  ' || status || ': ' || COUNT(*) FROM board_tasks GROUP BY status ORDER BY COUNT(*) DESC"
echo ""
echo "Events by source:"
query "SELECT '  ' || event_source || ': ' || COUNT(*) FROM events GROUP BY event_source ORDER BY COUNT(*) DESC"
echo ""
echo "Events by type (top 10):"
query "SELECT '  ' || event_type || ': ' || COUNT(*) FROM events GROUP BY event_type ORDER BY COUNT(*) DESC LIMIT 10"
echo ""
echo "Total economy GDP (all payouts):"
query "SELECT '  ' || COALESCE(SUM(amount),0) || ' coins' FROM bank_transactions WHERE type='escrow_release'"
echo ""

# Integrity checks
echo "Running integrity checks..."
ERRORS=0

LOCKED_TERMINAL=$(query "SELECT COUNT(*) FROM bank_escrow e JOIN board_tasks t ON e.task_id=t.task_id WHERE e.status='locked' AND t.status NOT IN ('open','accepted','submitted')")
if [[ "$LOCKED_TERMINAL" != "0" ]]; then
    echo "  ERROR: ${LOCKED_TERMINAL} locked escrows for terminal tasks"; ERRORS=$((ERRORS + 1))
else
    echo "  OK: All terminal task escrows resolved"
fi

NEG_BAL=$(query "SELECT COUNT(*) FROM bank_accounts WHERE balance < 0")
if [[ "$NEG_BAL" != "0" ]]; then
    echo "  ERROR: ${NEG_BAL} accounts with negative balance"; ERRORS=$((ERRORS + 1))
else
    echo "  OK: No negative balances"
fi

EVT_COUNT=$(query "SELECT COUNT(*) FROM events")
if [[ "$EVT_COUNT" -lt 1000 ]]; then
    echo "  WARNING: Only ${EVT_COUNT} events (target: 1000+)"
else
    echo "  OK: ${EVT_COUNT} events generated"
fi

FK_CHECK=$(query "PRAGMA foreign_key_check" 2>&1 || true)
if [[ -n "$FK_CHECK" ]]; then
    echo "  WARNING: Foreign key violations detected"
    echo "$FK_CHECK" | head -5
else
    echo "  OK: Foreign key integrity verified"
fi

echo ""
if [[ $ERRORS -eq 0 ]]; then
    echo "All checks passed. Database ready at: ${DB}"
else
    echo "${ERRORS} error(s) found."
    exit 1
fi
