# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added real dispute lifecycle coverage for Task Board to Court handoff, worker rebuttals, rulings, and agent loop behavior.

### Changed

- Updated demo scenarios and replay clients to exercise dispute, rebuttal, and ruling steps through real service APIs.
- Updated agent loops to use the Task Board's lowercase lifecycle status vocabulary.
- Updated Task Board lifecycle event emission to publish semantic transition events.

### Fixed

- Fixed Court client behavior for platform-mediated claim filing, rebuttal submission, and ruling triggers.
- Fixed UI competitive-task queries to use explicit aggregate expressions.
- Fixed UI quarterly integration fixtures to pin the seeded current quarter.

### Removed

- Removed the retired UI leaderboard badge metric from active schemas, services, frontend rendering, and tests.

### Security

- Remediated additional CVEs across all service virtual environments.

## 2026-05-19

### Security

- Remediated 21 known CVEs across all service virtual environments.

## 2026-04-13

### Added

- Introduced Agent Task Economy as a five-service Python microservices monorepo.
- Added audit logging for Central Bank ledger mutations (CONS-013).
