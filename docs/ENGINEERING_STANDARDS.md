# Engineering Standards

This documentation outlines the architectural guidelines, formatting expectations, and development conventions required to maintain high quality and developer velocity across the codebase.

## Backend Standards (Python 3.12+, FastAPI)
- **Async/Await Everywhere**: All IO operations (database access, redis, external web requests) must run asynchronously. Never block the event loop.
- **Strict Typing**: Leverage Python type annotations fully. Every public function signature must be fully typed. Use `mypy` for static analysis.
- **Pydantic v2 DTOs**: Ensure all API requests and responses are strictly validated through Pydantic data schemas.
- **Controller-Service Separation**: Keep FastAPI routers lean. Controllers must only parse requests, invoke service operations, and return response models. No business or database logic is allowed in routers.
- **Dependency Injection**: Utilize FastAPI's `Depends` system for injecting database sessions, redis clients, and service layer classes to allow easy unit testing and mocking.

## Frontend Standards (Next.js, TypeScript)
- **Feature-Based Monorepo Modules**: Group components, hooks, and types by feature domain (e.g., `components/charts`, `hooks/useWebsocket`).
- **Strict TypeScript**: Avoid `any` at all costs. Every component property, hook, state item, and external data contract must be fully typed.
- **State Management & Decoupled Hooks**: Use Zustand for lightweight global state (e.g., websocket connectivity, active symbol) and TanStack Query (React Query) for REST fetching.
- **WebSocket Isolation**: Wrap WebSocket management in a clean React Context/provider or state hook to prevent unnecessary component re-renders.

## Git & Quality Control
- **Conventional Commits**: Commit messages must adhere to standard prefix structures: `feat(scope): ...`, `fix(scope): ...`, `docs(scope): ...`, `refactor(scope): ...`.
- **Feature Isolation**: Never commit directly to `main`. Create branches from `main` using descriptive names (e.g., `feature/sprint1-market-ws`).
- **Comprehensive Unit Testing**: Write tests for all edge cases using `pytest` (backend) and `Vitest` (frontend). CI pipeline validation must pass before any merge.
