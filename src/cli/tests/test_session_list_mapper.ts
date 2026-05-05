import { mapAcpSessionInfo } from "../src/sessions/mapper.js";
import { SessionService } from "../src/sessions/service.js";
import { assert } from "./helpers.js";

const methods = {
  sessionRename: "_mustang.agent/session/rename",
  sessionArchive: "_mustang.agent/session/archive",
  sessionDelete: "_mustang.agent/session/delete",
} as const;

const mapped = mapAcpSessionInfo({
  sessionId: "abc123456789",
  cwd: "/tmp/project",
  title: null,
  updatedAt: "2026-04-28T00:00:00Z",
  _meta: {
    createdAt: "2026-04-27T00:00:00Z",
    totalInputTokens: 3,
    totalOutputTokens: 5,
    "mustang.agent/session": {
      archivedAt: "2026-04-28T01:00:00Z",
      titleSource: "auto",
    },
  },
});
assert(mapped.title === "project", "mapper should fallback title to cwd basename");
assert(mapped.path === "abc123456789", "mapper should project sessionId to path for UI compatibility");
assert(mapped.totalInputTokens === 3, "mapper should expose token metadata");
assert(mapped.archivedAt !== null, "mapper should expose archivedAt");

const calls: Array<{ method: string; params: any }> = [];
const client = {
  async request(method: string, params: any): Promise<any> {
    calls.push({ method, params });
    if (method === "session/list" && !params.cursor) {
      return { sessions: [{ sessionId: "one", title: "One" }], nextCursor: "next" };
    }
    if (method === "session/list") {
      return { sessions: [{ sessionId: "two", title: "Two" }], nextCursor: null };
    }
    if (method === methods.sessionRename) return { sessionId: params.sessionId, title: params.title };
    if (method === methods.sessionArchive) return { sessionId: params.sessionId, title: "Archived", archivedAt: params.archived ? "now" : null };
    if (method === methods.sessionDelete) return { deleted: true };
    if (method === "session/load") return { configOptions: [], modes: [] };
    throw new Error(`unexpected ${method}`);
  },
};

const service = new SessionService(client);
const sessions = await service.list({ cwd: "/tmp/project", includeArchived: true, limit: 2 });
assert(sessions.length === 2, "service should paginate session/list");
assert(calls[0].params.cwd === "/tmp/project", "service should pass cwd filter");
assert(calls[0].params._meta["mustang.agent/sessionFilters"].includeArchived === true, "service should pass includeArchived in _meta");

const renamed = await service.rename("one", "Renamed");
assert(renamed.title === "Renamed", "rename should map returned summary");
const archived = await service.archive("one", true);
assert(archived.archivedAt === "now", "archive should map returned archived summary");
const deleted = await service.delete("one", { force: true });
assert(deleted === true, "delete should return deleted flag");
assert(calls.at(-1)?.params.force === true, "delete should pass force");
const loaded = await service.load("one", "/tmp/project");
assert(loaded.sessionId === "one", "load should preserve the requested session id when response omits it");

console.log("PASS: session mapper and ACP service wrapper");
