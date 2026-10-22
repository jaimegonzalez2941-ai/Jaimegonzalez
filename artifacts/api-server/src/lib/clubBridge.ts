import { createConnection } from "node:net";
import path from "node:path";

export class BridgeError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// Resolved from the package, not workflow cwd. No bot credentials in Node.
const socketPath = process.env.CLUB_WEB_SOCKET ??
  path.resolve(import.meta.dirname, "../../..", ".data/club-web/bridge.sock");

export function clubBridge(request: Record<string, unknown>): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const socket = createConnection(socketPath);
    let bytes = 0;
    let data = "";
    let settled = false;
    const fail = () => {
      if (settled) return;
      settled = true;
      socket.destroy();
      reject(new BridgeError(503, "The bot connection is unavailable or interrupted. Check existing records before retrying an action; it may have completed."));
    };
    socket.setTimeout(50_000, fail);
    socket.on("error", fail);
    socket.on("end", () => { if (!settled) fail(); });
    socket.once("connect", () => socket.write(JSON.stringify(request) + "\n"));
    socket.on("data", (chunk: Buffer) => {
      bytes += chunk.length;
      if (bytes > 512 * 1024) return fail();
      data += chunk.toString("utf8");
      if (!data.includes("\n")) return;
      settled = true;
      socket.end();
      try {
        const response = JSON.parse(data.split("\n")[0]!);
        if (response.ok) resolve(response.data);
        else reject(new BridgeError(response.status ?? 500, String(response.error)));
      } catch {
        reject(new BridgeError(503, "The bot response could not be read. Check existing records before retrying."));
      }
    });
  });
}
