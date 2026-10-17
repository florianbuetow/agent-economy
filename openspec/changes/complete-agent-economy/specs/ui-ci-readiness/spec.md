# Spec Delta

## Purpose

Make UI unit and browser-test execution reproducible on developer machines and hosted CI, while preserving explicit production configuration and meaningful regression assertions.

## ADDED Requirements

### Requirement: Complete valid test configuration
UI tests that declare their configuration valid SHALL provide every required section and field explicitly, including the operator configuration. Their fixtures SHALL use isolated resources and SHALL NOT require a live economy merely to test configuration, health routing or successful initialization logging.

#### Scenario: Valid configuration reaches the tested behavior
- **WHEN** the configuration-loading, health-routing and successful operator-initialization tests run with valid fixtures
- **THEN** configuration validation succeeds and all original behavioral assertions execute and pass
- **AND** the fixtures explicitly provide the operator configuration path and handle
- **AND** successful initialization tests use controlled dependencies rather than an accidental connection to a real operator

#### Scenario: Required operator section remains required
- **WHEN** a UI configuration omits the complete operator section
- **THEN** configuration loading fails with an error identifying that missing section
- **AND** repairing valid test fixtures does not introduce a production default or implicit replacement for the absent section

### Requirement: Configuration rejection proves its intended cause
Negative UI configuration tests SHALL isolate the violation they claim to verify. Existing required-section checks and the separately documented partial-section behavior SHALL remain distinct regression contracts.

#### Scenario: Extra field is rejected for being extra
- **WHEN** an otherwise complete valid configuration contains an unsupported field
- **THEN** the reported validation failure identifies that unsupported field
- **AND** the test cannot pass solely because an unrelated required section is absent

#### Scenario: Missing-section matrix is preserved
- **WHEN** each required section is omitted in turn from an otherwise valid fixture
- **THEN** the corresponding negative test still observes the expected configuration failure
- **AND** fixture repair does not remove, skip or weaken those cases

### Requirement: Fixture cleanup survives failure
UI configuration and application fixtures SHALL restore environment overrides, cached settings and application state after success, assertion failure or setup failure so later tests do not inherit a prior test's configuration.

#### Scenario: One test fails before normal teardown
- **WHEN** a configuration or startup test fails while a temporary configuration override is active
- **THEN** the next test starts with its own explicit configuration and clean application state
- **AND** running the affected tests alone or in a different order produces the same result

### Requirement: Matching browser prerequisite is explicit
UI browser-dependent unit tests SHALL have a documented explicit preparation step for the browser revision required by the installed Playwright package. Local and hosted CI SHALL verify browser readiness before executing those tests, including after a dependency upgrade.

#### Scenario: Browser is absent or incompatible
- **WHEN** the required Chromium executable or headless-shell revision is absent or cannot launch
- **THEN** local CI exits nonzero at an early browser-prerequisite check with an actionable setup instruction
- **AND** browser-dependent tests are not skipped or reported as passing
- **AND** the failure is not mislabeled as a trend-rendering assertion failure

#### Scenario: Explicit browser preparation succeeds
- **WHEN** the documented browser setup is run for the installed dependency version
- **THEN** the readiness check launches the matching browser successfully
- **AND** the subsequent UI unit suite does not fail because an expected browser executable is missing

#### Scenario: Dependency upgrade changes browser revision
- **WHEN** an environment's Playwright package now requires a different browser revision
- **THEN** the same prerequisite check detects the mismatch before unit-test execution
- **AND** the documented setup procedure prepares the newly required revision

### Requirement: Real trend regression coverage is preserved
The increasing, decreasing and stable trend tests SHALL execute the shipped browser JavaScript and retain their expected direction and arrow assertions. A tooling repair SHALL NOT replace the browser behavior with a test-side reimplementation or suppress the tests.

#### Scenario: All trend states are evaluated in Chromium
- **WHEN** browser preparation and valid UI fixtures are in place
- **THEN** increasing renders an upward arrow with upward direction, decreasing renders a downward arrow with downward direction, and stable renders a neutral arrow with neutral direction
- **AND** all three assertions execute against the actual shipped JavaScript

#### Scenario: UI repair is verified before ticket closure
- **WHEN** the UI CI repair is proposed as complete
- **THEN** all originally reported health, configuration, logging and trend cases pass without skips
- **AND** the entire UI unit suite, UI quality gate and isolated root quality gate have fresh passing evidence
- **AND** historical user-supplied red output is retained separately from fresh reproduction and green proof
