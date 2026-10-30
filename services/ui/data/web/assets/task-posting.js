(function() {
  'use strict';

  var ATE = window.ATE;
  var RECORD_PREFIX = 'ate.task-posting:v1:';
  var COMPLETION_PREFIX = 'ate.task-posting-completion:v1:';
  var SAFE_INTEGER_MAX = 9007199254740991;
  var DURATION_MAX = 9223372036854;
  var OPERATION_PATTERN = /^op-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  var TASK_PATTERN = /^t-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
  var SHA256_PATTERN = /^[0-9a-f]{64}$/;
  var TIMESTAMP_PATTERN = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{6})Z$/;
  var BLOCK_REASONS = ['receipt_invalid', 'escrow_invalid', 'settlement_conflict',
    'publication_conflict', 'clock_out_of_range'];
  var browserConfig = null;
  var activeRecord = null;
  var recoveryInProgress = false;
  var recoveredChoiceRequired = false;
  var operatorBalance = null;
  var corruptRecordKeys = [];
  var browserConfigPromise = fetch('/api/proxy/browser-config').then(function(response) {
    if (!response.ok) throw new Error('Browser security configuration unavailable.');
    return response.json();
  }).then(function(value) {
    browserConfig = value;
    return value;
  });

  function ready() {
    return browserConfigPromise;
  }

  function postingFetch(url, body) {
    if (!browserConfig) throw new Error('Browser security configuration unavailable.');
    var headers = {'Content-Type': 'application/json'};
    headers[browserConfig.csrf_header_name] = browserConfig.csrf_header_value;
    return fetch(url, {method: 'POST', headers: headers, body: JSON.stringify(body)});
  }

  function exactKeys(value, keys) {
    if (!value || Object.getPrototypeOf(value) !== Object.prototype) return false;
    var actual = Object.keys(value).sort();
    var expected = keys.slice().sort();
    return actual.length === expected.length && actual.every(function(key, index) {
      return key === expected[index];
    });
  }

  function validPositiveInteger(value, maximum) {
    return Number.isSafeInteger(value) && value > 0 && value <= maximum;
  }

  function validText(value, maximum) {
    if (typeof value !== 'string' || value.length === 0) return false;
    for (var index = 0; index < value.length; index += 1) {
      var code = value.charCodeAt(index);
      if (code >= 0xD800 && code <= 0xDBFF) {
        if (index + 1 >= value.length) return false;
        var next = value.charCodeAt(index + 1);
        if (next < 0xDC00 || next > 0xDFFF) return false;
        index += 1;
      } else if (code >= 0xDC00 && code <= 0xDFFF) return false;
    }
    return maximum === null || Array.from(value).length <= maximum;
  }

  function validRecord(record) {
    if (!exactKeys(record, ['record_version', 'scope', 'intent', 'request_sha256']) ||
        record.record_version !== 1 || !SHA256_PATTERN.test(record.request_sha256)) return false;
    var scope = record.scope;
    if (!exactKeys(scope, ['schema_version', 'operator_agent_id', 'operator_public_fingerprint_sha256', 'service_urls']) ||
        scope.schema_version !== 1 || typeof scope.operator_agent_id !== 'string' || !scope.operator_agent_id ||
        !SHA256_PATTERN.test(scope.operator_public_fingerprint_sha256)) return false;
    var urls = scope.service_urls;
    var urlKeys = ['identity_url', 'bank_url', 'task_board_url', 'reputation_url', 'court_url'];
    if (!exactKeys(urls, urlKeys) || !urlKeys.every(function(key) {
      return typeof urls[key] === 'string' && urls[key].length > 0;
    })) return false;
    var intent = record.intent;
    var intentKeys = [
      'protocol_version', 'operation_id', 'task_id', 'actor_kind', 'actor_id',
      'lock_operation_id', 'title', 'spec', 'reward', 'bidding_deadline_seconds',
      'acceptance_window_seconds', 'execution_deadline_seconds', 'review_deadline_seconds'
    ];
    return exactKeys(intent, intentKeys) && intent.protocol_version === 1 &&
      OPERATION_PATTERN.test(intent.operation_id) && OPERATION_PATTERN.test(intent.lock_operation_id) &&
      typeof intent.task_id === 'string' && TASK_PATTERN.test(intent.task_id) &&
      intent.actor_kind === 'agent' && intent.actor_id === scope.operator_agent_id &&
      validText(intent.title, 200) && validText(intent.spec, 10000) &&
      validPositiveInteger(intent.reward, SAFE_INTEGER_MAX) &&
      validPositiveInteger(intent.bidding_deadline_seconds, DURATION_MAX) &&
      validPositiveInteger(intent.acceptance_window_seconds, DURATION_MAX) &&
      validPositiveInteger(intent.execution_deadline_seconds, DURATION_MAX) &&
      validPositiveInteger(intent.review_deadline_seconds, DURATION_MAX);
  }

  function numericField(id, label, maximum) {
    var raw = document.getElementById(id).value.trim();
    if (!/^[0-9]+$/.test(raw)) throw new Error(label + ' must be a positive decimal whole number.');
    var value = Number(raw);
    if (value <= 0) throw new Error(label + ' must be positive.');
    if (!validPositiveInteger(value, maximum)) {
      throw new Error(label + ' is outside the supported range.');
    }
    return value;
  }

  function termsFromForm() {
    var title = document.getElementById('f-title').value.trim();
    var spec = document.getElementById('f-spec').value.trim();
    if (!title || !spec) throw new Error('All fields are required.');
    return {
      title: title,
      spec: spec,
      reward: numericField('f-reward', 'Reward', SAFE_INTEGER_MAX),
      bidding_deadline_seconds: numericField('f-bid-dl', 'Bidding deadline', DURATION_MAX),
      acceptance_window_seconds: numericField('f-accept-window', 'Acceptance window', DURATION_MAX),
      execution_deadline_seconds: numericField('f-exec-dl', 'Execution deadline', DURATION_MAX),
      review_deadline_seconds: numericField('f-rev-dl', 'Review deadline', DURATION_MAX)
    };
  }

  function recordKey(record) {
    return RECORD_PREFIX + record.intent.operation_id;
  }

  function completionKey(record) {
    return COMPLETION_PREFIX + record.intent.operation_id;
  }

  function persistRecord(record) {
    var raw = JSON.stringify(record);
    var key = recordKey(record);
    var existing = localStorage.getItem(key);
    if (existing !== null && existing !== raw) throw new Error('Saved posting identity is immutable.');
    if (existing === null) localStorage.setItem(key, raw);
    if (localStorage.getItem(key) !== raw) throw new Error('Posting could not be verified in browser storage.');
    return raw;
  }

  function recordMatchesTerms(record, terms) {
    var intent = record.intent;
    return intent.title === terms.title && intent.spec === terms.spec &&
      intent.reward === terms.reward &&
      intent.bidding_deadline_seconds === terms.bidding_deadline_seconds &&
      intent.acceptance_window_seconds === terms.acceptance_window_seconds &&
      intent.execution_deadline_seconds === terms.execution_deadline_seconds &&
      intent.review_deadline_seconds === terms.review_deadline_seconds;
  }

  function canonicalJson(value) {
    if (Array.isArray(value)) return '[' + value.map(canonicalJson).join(',') + ']';
    if (value && Object.getPrototypeOf(value) === Object.prototype) {
      return '{' + Object.keys(value).sort().map(function(key) {
        return JSON.stringify(key) + ':' + canonicalJson(value[key]);
      }).join(',') + '}';
    }
    return JSON.stringify(value);
  }

  async function requestHash(intent) {
    var semantic = {};
    Object.keys(intent).forEach(function(key) {
      if (key !== 'operation_id') semantic[key] = intent[key];
    });
    var digest = await crypto.subtle.digest(
      'SHA-256',
      new TextEncoder().encode(canonicalJson(semantic))
    );
    return Array.from(new Uint8Array(digest)).map(function(value) {
      return value.toString(16).padStart(2, '0');
    }).join('');
  }

  function exactTimestamp(value) {
    if (typeof value !== 'string') return null;
    var match = TIMESTAMP_PATTERN.exec(value);
    if (match === null) return null;
    var year = Number(match[1]);
    var month = Number(match[2]);
    var day = Number(match[3]);
    var hour = Number(match[4]);
    var minute = Number(match[5]);
    var second = Number(match[6]);
    var microsecond = Number(match[7]);
    if (year < 1 || month < 1 || month > 12 || day < 1 || hour > 23 ||
        minute > 59 || second > 59) return null;
    var parsed = new Date(0);
    parsed.setUTCFullYear(year, month - 1, day);
    parsed.setUTCHours(hour, minute, second, Math.floor(microsecond / 1000));
    if (!Number.isFinite(parsed.getTime()) || parsed.getUTCFullYear() !== year ||
        parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day ||
        parsed.getUTCHours() !== hour || parsed.getUTCMinutes() !== minute ||
        parsed.getUTCSeconds() !== second ||
        parsed.getUTCMilliseconds() !== Math.floor(microsecond / 1000)) return null;
    var microseconds = BigInt(parsed.getTime()) * 1000n + BigInt(microsecond % 1000);
    if (microseconds < 0n || microseconds > 9223372036854775807n) return null;
    return {text: value, microseconds: microseconds};
  }

  function validBlockedError(value) {
    return exactKeys(value, ['error', 'message', 'details']) &&
      value.error === 'publication_integrity_blocked' &&
      value.message === 'Publication evidence requires reconciliation' &&
      exactKeys(value.details, ['reason']) &&
      BLOCK_REASONS.indexOf(value.details.reason) !== -1;
  }

  function validPublicationResult(result, publication, record, published) {
    var resultKeys = ['task_id', 'poster_id', 'title', 'spec', 'reward',
      'bidding_deadline_seconds', 'acceptance_window_seconds',
      'execution_deadline_seconds', 'review_deadline_seconds', 'status', 'escrow_id',
      'created_at', 'bidding_deadline', 'acceptance_deadline', 'bidding_cutoff_us',
      'acceptance_cutoff_us', 'lifecycle_version', 'timing_contract_version', 'event_id'];
    if (!exactKeys(result, resultKeys) || result.task_id !== record.intent.task_id ||
        result.poster_id !== record.intent.actor_id || !validText(result.poster_id, null) ||
        result.title !== record.intent.title || !validText(result.title, 200) ||
        result.spec !== record.intent.spec || !validText(result.spec, 10000) ||
        result.reward !== record.intent.reward ||
        result.bidding_deadline_seconds !== record.intent.bidding_deadline_seconds ||
        result.acceptance_window_seconds !== record.intent.acceptance_window_seconds ||
        result.execution_deadline_seconds !== record.intent.execution_deadline_seconds ||
        result.review_deadline_seconds !== record.intent.review_deadline_seconds ||
        result.status !== 'open' || result.escrow_id !== publication.escrow_id ||
        !validText(result.escrow_id, null) || result.created_at !== publication.published_at ||
        result.lifecycle_version !== 0 || result.timing_contract_version !== 2 ||
        !validPositiveInteger(result.event_id, SAFE_INTEGER_MAX)) return false;
    var created = exactTimestamp(result.created_at);
    var bidding = exactTimestamp(result.bidding_deadline);
    var acceptance = exactTimestamp(result.acceptance_deadline);
    if (created === null || bidding === null || acceptance === null ||
        created.microseconds !== published.microseconds ||
        !validPositiveInteger(result.bidding_cutoff_us, SAFE_INTEGER_MAX) ||
        !validPositiveInteger(result.acceptance_cutoff_us, SAFE_INTEGER_MAX)) return false;
    var expectedBidding = published.microseconds +
      BigInt(record.intent.bidding_deadline_seconds) * 1000000n;
    var expectedAcceptance = expectedBidding +
      BigInt(record.intent.acceptance_window_seconds) * 1000000n;
    return bidding.microseconds === expectedBidding &&
      acceptance.microseconds === expectedAcceptance &&
      BigInt(result.bidding_cutoff_us) === bidding.microseconds &&
      BigInt(result.acceptance_cutoff_us) === acceptance.microseconds;
  }

  function validPublication(publication, record, state) {
    var keys = ['protocol_version', 'operation_id', 'task_id', 'request_sha256', 'publication_state',
      'lock_operation_id', 'escrow_id', 'admitted_at', 'published_at', 'result', 'error'];
    if (!exactKeys(publication, keys) || publication.protocol_version !== 1 ||
        publication.operation_id !== record.intent.operation_id ||
        publication.task_id !== record.intent.task_id ||
        publication.request_sha256 !== record.request_sha256 ||
        publication.lock_operation_id !== record.intent.lock_operation_id ||
        publication.publication_state !== state ||
        !OPERATION_PATTERN.test(publication.operation_id) ||
        !TASK_PATTERN.test(publication.task_id) ||
        !OPERATION_PATTERN.test(publication.lock_operation_id) ||
        publication.operation_id === publication.lock_operation_id ||
        !SHA256_PATTERN.test(publication.request_sha256)) return false;
    var admitted = exactTimestamp(publication.admitted_at);
    if (admitted === null) return false;
    if (state === 'awaiting_lock') {
      return publication.escrow_id === null && publication.published_at === null &&
        publication.result === null && publication.error === null;
    }
    if (state === 'blocked_integrity') {
      return publication.escrow_id === null && publication.published_at === null &&
        publication.result === null && validBlockedError(publication.error);
    }
    if (!validText(publication.escrow_id, null) || publication.error !== null) return false;
    var published = exactTimestamp(publication.published_at);
    return published !== null && admitted.microseconds <= published.microseconds &&
      validPublicationResult(publication.result, publication, record, published);
  }

  function validateObservation(value, responseStatus, record, kind) {
    if (!exactKeys(value, ['observation', 'status_code', 'publication']) ||
        value.observation !== kind || value.status_code !== responseStatus) {
      throw new Error('Posting acknowledgment is malformed.');
    }
    var state = value.publication && value.publication.publication_state;
    var allowed = kind === 'status' ? responseStatus === 200 :
      (state === 'published' && responseStatus === 201) ||
      (state === 'awaiting_lock' && responseStatus === 202) ||
      (state === 'blocked_integrity' && responseStatus === 409);
    if (!allowed || ['published', 'awaiting_lock', 'blocked_integrity'].indexOf(state) === -1 ||
        !validPublication(value.publication, record, state)) {
      throw new Error('Posting acknowledgment does not match the saved intent.');
    }
    return value;
  }

  function errorElement() {
    return document.getElementById('post-error');
  }

  function operationElement(record) {
    var parent = errorElement();
    if (!parent) return null;
    var operationId = record.intent.operation_id;
    var selector = '[data-task-posting-operation="' + operationId + '"]';
    var element = parent.querySelector(selector);
    if (element) return element;
    element = document.createElement('span');
    element.dataset.taskPostingOperation = operationId;
    element.style.display = 'block';
    parent.appendChild(element);
    return element;
  }

  function generalErrorElement() {
    var parent = errorElement();
    if (!parent) return null;
    var element = parent.querySelector('[data-task-posting-general]');
    if (element) return element;
    element = document.createElement('span');
    element.dataset.taskPostingGeneral = '';
    element.style.display = 'block';
    parent.insertBefore(element, parent.firstChild);
    return element;
  }

  function renderCorruptRecord(key) {
    var parent = errorElement();
    if (!parent) return;
    var entries = parent.querySelectorAll('[data-task-posting-corrupt]');
    for (var index = 0; index < entries.length; index += 1) {
      if (entries[index].dataset.taskPostingCorrupt === key) return;
    }
    var element = document.createElement('span');
    element.dataset.taskPostingCorrupt = key;
    element.style.display = 'block';
    element.textContent = 'Saved posting record ' + key.slice(RECORD_PREFIX.length) +
      ' is invalid. It was preserved and was not sent.';
    parent.appendChild(element);
  }

  function appendSeparateAction(element) {
    var separate = document.createElement('button');
    separate.type = 'button';
    separate.className = 'btn btn-cyan';
    separate.textContent = 'Post as a new task';
    separate.title = 'Ignore the saved posting above and post the terms currently in the form as a new task.';
    separate.addEventListener('click', function() { createPosting(true); });
    element.appendChild(separate);
  }

  function recoveryText(error, fallback) {
    var base = error && error.message ? String(error.message) : fallback;
    if (!/[.!?]$/.test(base)) base += '.';
    return base + ' Next step: press "Check status".';
  }

  function renderCompleted(record) {
    var element = operationElement(record);
    if (!element) return;
    element.style.color = 'var(--green)';
    element.textContent = record.intent.title + ' — Posting completed. ';
    var link = document.createElement('a');
    link.href = '/task.html?task_id=' + encodeURIComponent(record.intent.task_id);
    link.textContent = 'Open the task';
    link.dataset.taskPostingLink = record.intent.task_id;
    element.appendChild(link);
  }

  function renderRecovery(record, message) {
    var element = operationElement(record);
    if (!element) return;
    element.style.color = '';
    element.textContent = record.intent.title + ' — ' + message + ' ';
    var check = document.createElement('button');
    check.type = 'button';
    check.className = 'btn btn-cyan';
    check.textContent = 'Check status';
    check.addEventListener('click', function() { checkStatus(record); });
    var retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn btn-cyan';
    retry.textContent = 'Try posting again';
    retry.title = 'Sends the same task again. It cannot create a duplicate.';
    retry.addEventListener('click', function() { submit(record); });
    element.appendChild(check);
    element.appendChild(retry);
    appendSeparateAction(element);
  }

  function existingCompletion(record) {
    var raw = localStorage.getItem(completionKey(record));
    if (raw === null) return null;
    try {
      var marker = JSON.parse(raw);
      if (!exactKeys(marker, ['scope', 'operation_id', 'task_id', 'request_sha256',
        'observed_status_code', 'publication']) ||
          JSON.stringify(marker.scope) !== JSON.stringify(record.scope) ||
          marker.operation_id !== record.intent.operation_id ||
          marker.task_id !== record.intent.task_id ||
          marker.request_sha256 !== record.request_sha256 ||
          [200, 201].indexOf(marker.observed_status_code) === -1 ||
          !validPublication(marker.publication, record, 'published')) return null;
      return marker;
    } catch (_error) { return null; }
  }

  function finishPosting(record) {
    if (activeRecord !== null && activeRecord.intent.operation_id === record.intent.operation_id) {
      activeRecord = null;
    }
    document.dispatchEvent(new CustomEvent('ate:posting-completed'));
  }

  function showCompleted(record) {
    renderCompleted(record);
    finishPosting(record);
  }

  function complete(record, observation) {
    var existing = existingCompletion(record);
    if (existing !== null) {
      showCompleted(record);
      return;
    }
    var publication = observation.publication;
    var marker = {
      scope: record.scope,
      operation_id: record.intent.operation_id,
      task_id: record.intent.task_id,
      request_sha256: record.request_sha256,
      observed_status_code: observation.status_code,
      publication: publication
    };
    var raw = JSON.stringify(marker);
    var key = completionKey(record);
    try {
      if (localStorage.getItem(key) === null) localStorage.setItem(key, raw);
      if (localStorage.getItem(key) !== raw && existingCompletion(record) === null) {
        throw new Error('Completion marker readback failed.');
      }
      showCompleted(record);
    } catch (_error) {
      renderRecovery(record, 'Your task was posted, but this browser could not save that fact. Next step: press "Check status" to confirm it.');
    }
  }

  async function observe(record, kind) {
    if (existingCompletion(record) !== null) {
      showCompleted(record);
      return;
    }
    var operationId = encodeURIComponent(record.intent.operation_id);
    var response = await postingFetch(
      '/api/proxy/task-postings/' + operationId + '/' + kind,
      record
    );
    var value = await response.json().catch(function() { return null; });
    if (!response.ok && response.status !== 409) {
      throw new Error(value && value.message ? value.message : 'Posting request failed (HTTP ' + response.status + ').');
    }
    var observation = validateObservation(value, response.status, record, kind);
    if (observation.publication.publication_state === 'published') complete(record, observation);
    else if (existingCompletion(record) !== null) showCompleted(record);
    else if (observation.publication.publication_state === 'blocked_integrity') {
      renderRecovery(record, 'Your task was not posted because the platform stopped it for a safety check, and your reward was not spent. Next step: press "Check status". If it stays blocked, ask the operator to review it.');
    } else renderRecovery(record, 'Your task is not posted yet. The platform is still locking the reward in escrow. Next step: press "Check status" in a few seconds.');
  }

  async function submit(record) {
    try { await observe(record, 'submit'); }
    catch (error) { renderRecovery(record, recoveryText(error, 'The platform did not answer, so your task may or may not be posted.')); }
  }

  async function checkStatus(record) {
    try { await observe(record, 'status'); }
    catch (error) { renderRecovery(record, recoveryText(error, 'The posting status could not be read.')); }
  }

  async function createPosting(forceSeparate) {
    var button = document.getElementById('btn-post-task');
    var element = generalErrorElement();
    try {
      if (activeRecord !== null && existingCompletion(activeRecord) !== null) activeRecord = null;
      if (activeRecord !== null && forceSeparate !== true) {
        await submit(activeRecord);
        return;
      }
      if (forceSeparate !== true && recoveryInProgress) {
        if (element) {
          element.textContent = 'Checking saved posting status before another posting can begin.';
        }
        return;
      }
      if (forceSeparate !== true && corruptRecordKeys.length > 0) {
        if (element) {
          element.textContent = 'Resolve the invalid saved posting record before creating a new posting.';
        }
        return;
      }
      if (forceSeparate !== true && recoveredChoiceRequired) {
        if (element) {
          element.textContent = 'Choose the saved posting you want to retry using its exact-intent action.';
        }
        return;
      }
      var terms = termsFromForm();
      if (operatorBalance !== null && terms.reward > operatorBalance) {
        throw new Error('The reward (' + terms.reward + ' \u00a9) is more than your balance (' +
          operatorBalance + ' \u00a9). The reward is locked in escrow when you post, so lower the reward or add funds first.');
      }
      if (button) button.disabled = true;
      if (element) element.textContent = '';
      var response = await postingFetch('/api/proxy/task-postings/prepare', terms);
      var record = await response.json().catch(function() { return null; });
      if (!response.ok) throw new Error(record && record.message ? record.message : 'Posting preparation failed.');
      if (!validRecord(record)) throw new Error('Prepared posting record is malformed.');
      if (!recordMatchesTerms(record, terms)) {
        throw new Error('Prepared posting record does not match the submitted terms.');
      }
      if (await requestHash(record.intent) !== record.request_sha256) {
        throw new Error('Prepared posting record has an invalid request hash.');
      }
      persistRecord(record);
      activeRecord = record;
      recoveredChoiceRequired = false;
      await submit(record);
    } catch (error) {
      if (element) element.textContent = error.message || 'Posting could not be prepared.';
    } finally {
      if (button) button.disabled = false;
    }
  }

  function bindCreateForm() {
    var button = document.getElementById('btn-post-task');
    if (button) button.addEventListener('click', function() { createPosting(false); });
  }

  async function recoverExisting() {
    var records = [];
    recoveryInProgress = true;
    try {
      corruptRecordKeys = [];
      recoveredChoiceRequired = false;
      for (var index = 0; index < localStorage.length; index += 1) {
        var key = localStorage.key(index);
        if (!key || key.indexOf(RECORD_PREFIX) !== 0) continue;
        try {
          var record = JSON.parse(localStorage.getItem(key));
          if (validRecord(record) && key === recordKey(record)) records.push(record);
          else {
            corruptRecordKeys.push(key);
            renderCorruptRecord(key);
          }
        } catch (_error) {
          corruptRecordKeys.push(key);
          renderCorruptRecord(key);
        }
      }
      for (var recordIndex = 0; recordIndex < records.length; recordIndex += 1) {
        var record = records[recordIndex];
        if (existingCompletion(record) === null) await checkStatus(record);
      }
      var unresolved = records.filter(function(record) {
        return existingCompletion(record) === null;
      });
      if (corruptRecordKeys.length > 0) activeRecord = null;
      else if (unresolved.length === 1) activeRecord = unresolved[0];
      else if (unresolved.length > 1) {
        activeRecord = null;
        recoveredChoiceRequired = true;
      } else activeRecord = null;
    } finally {
      recoveryInProgress = false;
    }
  }

  function setBalance(value) {
    operatorBalance = Number.isSafeInteger(value) && value >= 0 ? value : null;
  }

  ATE.TaskPosting = {
    ready: ready,
    setBalance: setBalance,
    bindCreateForm: bindCreateForm,
    recoverExisting: recoverExisting
  };
})();
