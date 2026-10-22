import app from "./app";
import { logger } from "./lib/logger";

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error(
    "PORT environment variable is required but was not provided.",
  );
}

const port = Number(rawPort);

if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error("PORT must be an integer between 1 and 65535.");
}

const server = app.listen(port);

server.once("listening", () => {
  logger.info({ port }, "Server listening");
});

server.on("error", (err) => {
  logger.error({ err }, "Error listening on port");
  process.exit(1);
});

const shutdown = (signal: NodeJS.Signals) => {
  logger.info({ signal }, "Stopping API server gracefully");
  const timeout = setTimeout(() => {
    logger.warn({ signal }, "API shutdown timed out; closing remaining connections");
    server.closeAllConnections();
  }, 10_000);
  timeout.unref();
  server.once("close", () => clearTimeout(timeout));
  server.close((err) => {
    if (err) {
      logger.error({ err, signal }, "API server shutdown failed");
      process.exitCode = 1;
    }
  });
};

process.once("SIGTERM", () => shutdown("SIGTERM"));
process.once("SIGINT", () => shutdown("SIGINT"));
