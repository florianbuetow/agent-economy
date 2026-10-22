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
  var browserConfig = null;
  var activeRecord = null;
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
      typeof intent.title === 'string' && intent.title.length > 0 && intent.title.length <= 200 &&
      typeof intent.spec === 'string' && intent.spec.length > 0 && intent.spec.length <= 10000 &&
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

  function validPublication(publication, record, state) {
    var keys = ['protocol_version', 'operation_id', 'task_id', 'request_sha256', 'publication_state',
      'lock_operation_id', 'escrow_id', 'admitted_at', 'published_at', 'result', 'error'];
    if (!exactKeys(publication, keys) || publication.protocol_version !== 1 ||
        publication.operation_id !== record.intent.operation_id ||
        publication.task_id !== record.intent.task_id ||
        publication.request_sha256 !== record.request_sha256 ||
        publication.lock_operation_id !== record.intent.lock_operation_id ||
        publication.publication_state !== state) return false;
    if (state !== 'published') return publication.result === null;
    var result = publication.result;
    var resultKeys = ['task_id', 'poster_id', 'title', 'spec', 'reward',
      'bidding_deadline_seconds', 'acceptance_window_seconds',
      'execution_deadline_seconds', 'review_deadline_seconds', 'status', 'escrow_id',
      'created_at', 'bidding_deadline', 'acceptance_deadline', 'bidding_cutoff_us',
      'acceptance_cutoff_us', 'lifecycle_version', 'timing_contract_version', 'event_id'];
    return exactKeys(result, resultKeys) && result.task_id === record.intent.task_id &&
      result.poster_id === record.intent.actor_id && result.title === record.intent.title &&
      result.spec === record.intent.spec && result.reward === record.intent.reward &&
      result.bidding_deadline_seconds === record.intent.bidding_deadline_seconds &&
      result.acceptance_window_seconds === record.intent.acceptance_window_seconds &&
      result.execution_deadline_seconds === record.intent.execution_deadline_seconds &&
      result.review_deadline_seconds === record.intent.review_deadline_seconds;
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

  function renderCompleted(record) {
    var element = errorElement();
    if (element) element.innerHTML = '<span>Posting completed.</span>';
  }

  function renderRecovery(record, message) {
    var element = errorElement();
    if (!element) return;
    element.textContent = message + ' ';
    var check = document.createElement('button');
    check.type = 'button';
    check.className = 'btn btn-cyan';
    check.textContent = 'Check status';
    check.addEventListener('click', function() { checkStatus(record); });
    var retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn btn-cyan';
    retry.textContent = 'Retry exact intent';
    retry.addEventListener('click', function() { submit(record); });
    var separate = document.createElement('button');
    separate.type = 'button';
    separate.className = 'btn btn-cyan';
    separate.textContent = 'Create separate posting';
    separate.addEventListener('click', function() { createPosting(true); });
    element.appendChild(check);
    element.appendChild(retry);
    element.appendChild(separate);
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

  function complete(record, observation) {
    var existing = existingCompletion(record);
    if (existing !== null) {
      renderCompleted(record);
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
      renderCompleted(record);
    } catch (_error) {
      renderRecovery(record, 'Publication succeeded, but completion could not be saved.');
    }
  }

  async function observe(record, kind) {
    if (existingCompletion(record) !== null) {
      renderCompleted(record);
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
    else if (existingCompletion(record) !== null) renderCompleted(record);
    else if (observation.publication.publication_state === 'blocked_integrity') {
      renderRecovery(record, 'Posting is blocked by an integrity finding.');
    } else renderRecovery(record, 'Posting is pending.');
  }

  async function submit(record) {
    try { await observe(record, 'submit'); }
    catch (error) { renderRecovery(record, error.message || 'Posting response was not received.'); }
  }

  async function checkStatus(record) {
    try { await observe(record, 'status'); }
    catch (error) { renderRecovery(record, error.message || 'Posting status is unavailable.'); }
  }

  async function createPosting(forceSeparate) {
    var button = document.getElementById('btn-post-task');
    var element = errorElement();
    try {
      if (activeRecord !== null && forceSeparate !== true) {
        await submit(activeRecord);
        return;
      }
      var terms = termsFromForm();
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
    for (var index = 0; index < localStorage.length; index += 1) {
      var key = localStorage.key(index);
      if (!key || key.indexOf(RECORD_PREFIX) !== 0) continue;
      try {
        var record = JSON.parse(localStorage.getItem(key));
        if (validRecord(record) && key === recordKey(record)) records.push(record);
      } catch (_error) { /* Invalid browser-owned entries never reach transport. */ }
    }
    records.sort(function(left, right) {
      return left.intent.operation_id.localeCompare(right.intent.operation_id);
    });
    if (records.length > 0) activeRecord = records[records.length - 1];
    for (var recordIndex = 0; recordIndex < records.length; recordIndex += 1) {
      var record = records[recordIndex];
      if (existingCompletion(record) !== null) renderCompleted(record);
      else await checkStatus(record);
    }
  }

  ATE.TaskPosting = {
    ready: ready,
    bindCreateForm: bindCreateForm,
    recoverExisting: recoverExisting
  };
})();
