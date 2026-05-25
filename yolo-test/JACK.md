# JACK Project Profile — yolo-test

**Project**: YOLO Demo — Earth Globe Web App (POC Test Subproject)
**Generated**: 2026-05-25
**Maturity**: POC (Proof of Concept)

---

## Overview

yolo-test is a simple web app demo and test fixture for the YOLO plugin.
It demonstrates the autonomous work mode capability with a minimal, self-contained earth globe visualization and interaction model.
This folder is treated as an independent repo root for development purposes.

### Key Signals

| Signal | Value | Rationale |
|--------|-------|-----------|
| **Product Type** | Web App (Demo) | Simple interactive browser app, not a library |
| **Maturity** | POC | Experimental; demo-grade code, not hardened |
| **Language** | TypeScript | Frontend app with TypeScript |
| **Test Framework** | None formal | Manual testing sufficient for POC demo |
| **TDD Policy** | Off | Fast iteration; tests written after design |
| **Rigor Level** | Normal | Lint, code review; no mutation testing |

---

## Development Approach

### Jack Integration

Jack's workflow applies lightly here—no hard TDD requirement, but all code must pass lint and code review before commit.
Use `/commit` or `/commitall` for atomic, signed commits.

### What Jack Expects

1. **No TDD cycles required** — write code first, validate after
2. **Normal rigor** — lint and code review before commit
3. **Atomic commits** — each commit is logically grouped
4. **Clean code** — readable, maintainable, no dead code

### What Jack Won't Do

- Force test-first development (TDD is off)
- Require exhaustive test coverage (POC demo)
- Demand deployment pipelines (containerization: none)

---

## POC Demo Scope

This is a lightweight, self-contained demo.
Treat it as a throwaway or reference implementation.
Performance, scalability, and production-grade error handling are secondary to clarity and speed.

---

## File Structure

Assume this folder is the repo root for all development.
No references to parent YOLO plugin infrastructure.

---

*Initialized via `/jack:init` on 2026-05-25.*
