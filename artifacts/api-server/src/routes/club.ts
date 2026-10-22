import { Router, type Request, type Response, type NextFunction } from "express";
import { getAuth } from "@clerk/express";
import { GetClubWorkspaceQueryParams, RunClubActionBody, GetPlayerDevelopmentQueryParams,
  GetPlayerDevelopmentResponse, GetPlayerAccessResponse } from "@workspace/api-zod";
import { BridgeError, clubBridge } from "../lib/clubBridge";
import { isSameOriginMutation } from "../lib/browserOrigin";

export const clubRouter = Router();

clubRouter.use((_req, res, next) => {
  res.set("Cache-Control", "no-store, private");
  res.set("Pragma", "no-cache");
  res.set("Vary", "Cookie, Origin");
  res.set("X-Content-Type-Options", "nosniff");
  next();
});

clubRouter.get("/public", async (_req, res) => {
  try {
    res.json(await clubBridge({ kind: "public" }));
  } catch {
    res.json({ connected: false, installUrl: null, message: "Bot connection unavailable. Installation and live checks cannot currently be verified." });
  }
});

clubRouter.use((req: Request, res: Response, next: NextFunction) => {
  if (!getAuth(req).userId) {
    res.status(401).json({ error: "Sign in to your own website account first." });
    return;
  }
  // Mutations require same-origin fetches and JSON. Cookie identity alone is
  // insufficient against cross-site POSTs. Never use wildcard credentialed CORS.
  if (req.method !== "GET") {
    const origin = req.get("origin");
    const host = req.get("x-forwarded-host")?.split(",")[0]?.trim() ?? req.get("host");
    if (!isSameOriginMutation({origin, host, fetchSite: req.get("sec-fetch-site"),
      contentType: req.get("content-type"), path: req.path})) {
      res.status(403).json({ error: "Submit this action from the signed-in Club Agent website." });
      return;
    }
  }
  next();
});

clubRouter.get("/access", async (req, res, next) => {
  try {
    res.json(await clubBridge({ kind: "access", subject: getAuth(req).userId }));
  } catch (error) { next(error); }
});
clubRouter.get("/player/access", async (req, res, next) => {
  if (Object.keys(req.query).length) {
    res.status(400).json({ error: "Player access does not accept identity or server overrides." });
    return;
  }
  try {
    res.json(GetPlayerAccessResponse.parse(
      await clubBridge({ kind: "player.access", subject: getAuth(req).userId })));
  } catch (error) { next(error); }
});
clubRouter.get("/player/development", async (req, res, next) => {
  const parsed = GetPlayerDevelopmentQueryParams.strict().safeParse({
    ...req.query, page: req.query.page === undefined ? 1 : Number(req.query.page),
  });
  if (!parsed.success) {
    res.status(400).json({ error: "Choose a member server and valid page. Player identity cannot be supplied." });
    return;
  }
  try {
    res.json(GetPlayerDevelopmentResponse.parse(
      await clubBridge({ kind: "player.development", subject: getAuth(req).userId,
        guild: parsed.data.guild, params: parsed.data })));
  } catch (error) { next(error); }
});
clubRouter.post("/link", async (req, res, next) => {
  try {
    res.json(await clubBridge({ kind: "link", subject: getAuth(req).userId }));
  } catch (error) { next(error); }
});
clubRouter.get("/workspace", async (req, res, next) => {
  const parsed = GetClubWorkspaceQueryParams.safeParse({
    ...req.query, page: req.query.page === undefined ? 1 : Number(req.query.page),
  });
  if (!parsed.success) {
    res.status(400).json({ error: "Choose a server, workflow and valid page/filter." });
    return;
  }
  try {
    res.json(await clubBridge({ kind: "workspace", subject: getAuth(req).userId,
      guild: parsed.data.guild, params: parsed.data }));
  } catch (error) { next(error); }
});
clubRouter.post("/action", async (req, res, next) => {
  const parsed = RunClubActionBody.strict().safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "The operation fields could not be read. Reopen the form and try again." });
    return;
  }
  try {
    res.json(await clubBridge({ kind: "action", subject: getAuth(req).userId, ...parsed.data }));
  } catch (error) { next(error); }
});
clubRouter.use((error: unknown, req: Request, res: Response, _next: NextFunction) => {
  if (error instanceof BridgeError) {
    res.status(error.status).json({ error: error.message });
    return;
  }
  req.log.error({ err: error }, "Website API failed");
  res.status(500).json({ error: "This request could not be confirmed. Check existing records before retrying." });
});
