// Ports and token of the `make run-fake` stack the end-to-end test starts. The ports
// differ from the defaults, so a running `make dev` or `make run-fake` does not clash.
export const STUDIO_PORT = 18080;
export const WORKER_PORT = 18000;
export const STUDIO_URL = `http://127.0.0.1:${STUDIO_PORT.toString()}`;
export const WORKER_URL = `http://127.0.0.1:${WORKER_PORT.toString()}`;
/** A fixed test-only token (the worker needs at least 32 characters). */
export const WORKER_KEY = "e2e0test0token0not0a0secret0000000000000000000000000000000000000";
