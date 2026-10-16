import { createApiClient } from "./api/client";
import type { components } from "./api/schema";
import { CONTROL_TOKEN_HEADER, readControlToken } from "./controlToken";
import { errorMessageOf } from "./lib/errorMessage";

export interface ParsedSseEvent {
  event: string;
  data: string;
}

export type AssumptionStatus = "open" | "accepted" | "challenged";

export interface Assumption {
  id: string;
  text: string;
  status: AssumptionStatus;
}

export interface WorkspaceEventHandlers {
  onEvent: (event: ParsedSseEvent) => void;
  onError: (detail: string) => void;
}

export type StreamEvent = Record<string, unknown> & { type?: string };

export type NodeDetailTab = "output" | "prompt" | "config";

export type DispatchInstructionsMap = Readonly<Record<string, DispatchInstructionsReadback>>;

export type WorkspaceStatus = components["schemas"]["Status"];
export type Requirement = components["schemas"]["Requirement"];
export type RequirementStatus = Requirement["status"];
export type PlanningState = components["schemas"]["PlanningState"];
export type DoDClause = components["schemas"]["TaskDoDClause"];
export type TaskStatus = components["schemas"]["TaskStatus"];
export type Task = components["schemas"]["Task"];
export type InjectedTask = components["schemas"]["InjectedTask"];
export type UnblockRecord = components["schemas"]["UnblockRecord"];
export type WorkspaceSummary = components["schemas"]["WorkspaceSummary"];
export type Decision = components["schemas"]["Decision"];
export type OpenQuestion = components["schemas"]["OpenQuestion"];
export type QuestionClass = NonNullable<OpenQuestion["question_class"]>;
export type OptionEffect = NonNullable<OpenQuestion["option_effects"]>[string];
export type ArtifactRef = components["schemas"]["ArtifactRef"];
export type TestScenario = components["schemas"]["TestScenario"];
export type ScenarioOutcome = components["schemas"]["ScenarioOutcome"];
export type Handover = components["schemas"]["Handover"];
export type WorkspaceDelivery = components["schemas"]["WorkspaceDelivery"];
export type ManagerSummary = components["schemas"]["ManagerSummary"];
export type TerminalOutcome = components["schemas"]["TerminalOutcome"];
export type ParkReason = components["schemas"]["ParkReason"];
export type Blocker = components["schemas"]["Blocker"];
export type BlockerKind = Blocker["kind"];
export type WorkspaceCostBreakdownEntry = components["schemas"]["WorkspaceCostBreakdownEntry"];
export type PrepChatRef = components["schemas"]["PrepChatRef"];
export type PendingDeployGate = components["schemas"]["PendingDeployGate"];
export type PendingDeployGatePath = PendingDeployGate["path"];
export type WorkspaceDetail = components["schemas"]["WorkspaceDetail"];
export type HistoryEntry = WorkspaceDetail["history"][number];
export type WorkflowTask = components["schemas"]["WorkflowTask"];
export type WorkflowDoc = components["schemas"]["WorkflowDoc"];
export type NodeStatus = WorkflowTask["status"];
export type WorkflowStatus = WorkflowDoc["status"];
export type NodeResultDoc = components["schemas"]["NodeResultDoc"];
export type NodeConfigDoc = components["schemas"]["NodeConfigDoc"];
export type DispatchInstructionsReadback = components["schemas"]["DispatchInstructionsReadback"];
export type DispatchInstructionsStatus = DispatchInstructionsReadback["status"];
export type InstructionOrigin = NonNullable<DispatchInstructionsReadback["origin"]>;
export type StickyInstructionOut = components["schemas"]["StickyInstructionOut"];
export type StickyInstructionOrigin = NonNullable<StickyInstructionOut["origin"]>;

export type HealthFinding = components["schemas"]["HealthFinding"];
export type HealthState = components["schemas"]["HealthStateResponse"];
export type FinisherFunnelCounts = components["schemas"]["FunnelCounts"];
export type FinisherLeakReasons = components["schemas"]["LeakReasons"];
export type FinisherRate = components["schemas"]["FinisherRate"];
export type QualityMetrics = components["schemas"]["QualityMetrics"];
export type SweeperAction = components["schemas"]["RecentSweeperAction"];
export type Sweeper = components["schemas"]["SweeperStatus"];
export type HealthResponse = components["schemas"]["HealthResponse"];
export type RepoHealthValue = components["schemas"]["RepoRateValue"];
export type RepoHealthRow = components["schemas"]["RepoHealthRow"];
export type RepoHealthResponse = components["schemas"]["RepoHealthResponse"];
export type HealthWindow = components["schemas"]["HealthWindow"];
export type HealthMetricWindows = components["schemas"]["HealthMetricWindows"];
export type HealthScopeWindows = components["schemas"]["HealthScopeWindows"];
export type HealthVerdict = components["schemas"]["Verdict"];
export type RecoveryTrendDirection = components["schemas"]["RecoverySnapshot"]["direction"];
export type RecoverySnapshot = components["schemas"]["RecoverySnapshot"];
export type ResidueItem = components["schemas"]["ResidueItem"];
export type HealthDigestResponse = components["schemas"]["DigestSnapshotResponse"];
export type UnblockOutcomeCount = components["schemas"]["UnblockOutcomeCount"];
export type UnblockShapeAggregate = components["schemas"]["UnblockShapeAggregate"];
export type UnblockDiagnosis = components["schemas"]["UnblockDiagnosis"];
export type UnblockAttemptIndex = components["schemas"]["UnblockAttemptIndex"];
export type UnblockClearance = components["schemas"]["UnblockActivityEvent"]["clearance"];
export type UnblockActivityEvent = components["schemas"]["UnblockActivityEvent"];
export type UnblockWorkspaceActivity = components["schemas"]["UnblockWorkspaceActivity"];
export type UnblockFleetActivity = components["schemas"]["UnblockFleetActivity"];
export type UnblockScopedActivity = components["schemas"]["UnblockScopedActivity"];
export type UnblockActivityResponse = UnblockFleetActivity | UnblockScopedActivity;
export type DriverLoopDecisionAction = components["schemas"]["DriverLoopDecisionOut"]["action"];
export type DriverLoopHoldReason = components["schemas"]["HoldReason"];
export type DriverLoopDecision = components["schemas"]["DriverLoopDecisionOut"];
export type DriverLoopOutcome = components["schemas"]["RunOutcome"];
export type DriverLoopStatus = components["schemas"]["DriverLoopStatusOut"];
export type DriverLoopsResponse = components["schemas"]["DriverLoopsListOut"];
export type DriverLoopPauseAck = components["schemas"]["DriverLoopPauseAck"];
export type QueueResumeAck = components["schemas"]["QueueResumeAck"];

export type Project = components["schemas"]["Project"];
export type ProjectStatus = components["schemas"]["ProjectStatus"];
export type ProjectAutonomy = Project["autonomy"];
export type ProjectLedgerEntry = components["schemas"]["ProjectLedgerEntry"];
export type RoadmapItemView = components["schemas"]["RoadmapItemView"];
export type RoadmapDeliveryStatus = RoadmapItemView["status"];
export type ProjectBurn = components["schemas"]["ProjectBurn"];
export type ProjectBudgetView = components["schemas"]["ProjectBudgetView"];
export type BudgetState = ProjectBudgetView["state"];
export type ProjectDetail = components["schemas"]["ProjectDetail"];
export type ProjectListEntry = components["schemas"]["ProjectListEntry"];
export type ProjectDoc = components["schemas"]["ProjectDocResponse"];
export type ProjectAmendmentProposal = components["schemas"]["ProposeAmendmentResponse"];
export type RoadmapPreviewItem = components["schemas"]["RoadmapPreviewItem"];
export type RoadmapPreviewResponse = components["schemas"]["RoadmapPreviewResponse"];
export type RoadmapItem = components["schemas"]["RoadmapItem"];
export type RoadmapImportResponse = components["schemas"]["RoadmapImportResponse"];
export type ProposeRoadmapEditPayload = components["schemas"]["RoadmapEditBody"];
export type RoadmapAdvanceResult = components["schemas"]["RoadmapAdvanceResponse"];
export type InboxAction = components["schemas"]["InboxAction"];
export type InboxActionKind = InboxAction["kind"];
export type ProposalItem = components["schemas"]["ProposalItem"];
export type RoadmapNextItemProposalResponse = components["schemas"]["RoadmapNextItemProposalResponse"];

const WORKSPACES_PATH = "/api/workspaces";
const JSON_CONTENT_TYPE = "Content-Type";
const JSON_TYPE = "application/json";

const TASK_ID_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const INV_ID_RE = /^inv_\d{3}$/;
const ROLE_RE = /^[a-z][a-z0-9_]{0,63}$/;
const NODE_ID_RE = /^[a-z][a-z0-9_]{0,63}$/;
const SESSION_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SAFE_URL_PATH_RE = /^\/api\/[A-Za-z0-9_./-]*$/;

function assertSafeSegment(re: RegExp, value: string, name: string): void {
  if (!re.test(value)) {
    throw new Error(`invalid ${name}: ${value}`);
  }
}

function assertSafeUrlPath(path: string): void {
  if (!SAFE_URL_PATH_RE.test(path)) {
    throw new Error(`unsafe request path: ${path}`);
  }
}

function pickStringField(obj: object, key: string): string {
  const v = (obj as Record<string, unknown>)[key];
  return typeof v === "string" ? v : "";
}

function flattenStructuredDetail(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const code = pickStringField(value, "detail");
  const reason = pickStringField(value, "reason");
  if (code && reason) return `${code}: ${reason}`;
  return code || reason || null;
}

function detailFromBody(body: string): string {
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
    const flattened = flattenStructuredDetail(parsed.detail);
    if (flattened !== null) return flattened;
  } catch {
    // body wasn't JSON — fall through with raw text as detail
  }
  return body;
}

async function readErrorDetail(res: Response): Promise<string> {
  const body = await res.text();
  return detailFromBody(body) || res.statusText;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

function sanitizeApiUrl(path: string): URL {
  assertSafeUrlPath(path);
  return new URL(path, globalThis.location.origin);
}

const apiClient = createApiClient({
  baseUrl: globalThis.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

function detailField(error: unknown): string | null {
  if (!error || typeof error !== "object") return null;
  const { detail } = error as { detail?: unknown };
  return typeof detail === "string" ? detail : flattenStructuredDetail(detail);
}

function detailFromValue(error: unknown): string | null {
  const detail = typeof error === "string" ? error : detailField(error);
  return detail === "" ? null : detail;
}

interface ClientResult<T> {
  data?: T | undefined;
  error?: unknown;
  response: Response;
}

function unwrap<T>({ data, error, response }: ClientResult<T>): T {
  if (!response.ok) {
    throw new ApiError(response.status, detailFromValue(error) ?? response.statusText);
  }
  return data as T;
}

const DRIVER_LOOP_NAME_RE = /^[a-z][a-z0-9-]*$/;

function driverLoopParams(name: string) {
  assertSafeSegment(DRIVER_LOOP_NAME_RE, name, "name");
  return { params: { path: { name } } };
}

function engineParams(engineKey: string) {
  return { params: { path: engineOwnerRepo(engineKey) } };
}

function taskParams(taskId: string) {
  assertSafeSegment(TASK_ID_RE, taskId, "task_id");
  return { params: { path: { task_id: taskId } } };
}

function invocationPath(taskId: string, invId: string) {
  assertSafeSegment(TASK_ID_RE, taskId, "task_id");
  assertSafeSegment(INV_ID_RE, invId, "inv_id");
  return { task_id: taskId, inv_id: invId };
}

async function getHealth(): Promise<HealthResponse> {
  return unwrap(await apiClient.GET("/api/health"));
}

async function getHealthRepos(): Promise<RepoHealthResponse> {
  return unwrap(await apiClient.GET("/api/health/repos"));
}

async function getHealthDigest(): Promise<HealthDigestResponse> {
  return unwrap(await apiClient.GET("/api/health/digest"));
}

async function getUnblockerActivity(engine: string | null): Promise<UnblockActivityResponse> {
  return unwrap(
    await apiClient.GET("/api/health/unblocker-activity", { params: { query: { engine } } }),
  );
}

async function getDriverLoops(): Promise<DriverLoopsResponse> {
  return unwrap(await apiClient.GET("/api/driver-loops"));
}

async function pauseDriverLoop(name: string): Promise<DriverLoopPauseAck> {
  return unwrap(await apiClient.POST("/api/driver-loops/{name}/pause", driverLoopParams(name)));
}

async function resumeDriverLoop(name: string): Promise<DriverLoopPauseAck> {
  return unwrap(await apiClient.POST("/api/driver-loops/{name}/resume", driverLoopParams(name)));
}

async function runDriverLoopNow(name: string): Promise<DriverLoopStatus> {
  return unwrap(await apiClient.POST("/api/driver-loops/{name}/run-now", driverLoopParams(name)));
}

async function resumeQueue(): Promise<QueueResumeAck> {
  return unwrap(await apiClient.POST("/api/queue/resume"));
}

function parseSseFrame(frame: string): ParsedSseEvent {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data = line.slice(5).trim();
  }
  return { event, data };
}

export function parseSseChunk(buffer: string): { events: ParsedSseEvent[]; remainder: string } {
  const events: ParsedSseEvent[] = [];
  const lastBoundary = buffer.lastIndexOf("\n\n");
  if (lastBoundary === -1) return { events, remainder: buffer };
  const remainder = buffer.slice(lastBoundary + 2);
  for (const frame of buffer.slice(0, lastBoundary).split("\n\n")) {
    if (frame) events.push(parseSseFrame(frame));
  }
  return { events, remainder };
}

async function pumpSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: ParsedSseEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, remainder } = parseSseChunk(buffer);
    buffer = remainder;
    for (const evt of events) onEvent(evt);
  }
}

function trimmedClauses(clauses: string[] | undefined): string[] {
  return (clauses ?? []).map((c) => c.trim()).filter((c) => c.length > 0);
}

function deriveRequirementsFromAcceptanceClauses(
  acceptanceClauses?: string[],
  recordedClauses?: string[],
): { text: string; kind?: "record" }[] | null {
  const derived = [
    ...trimmedClauses(acceptanceClauses).map((text) => ({ text })),
    ...trimmedClauses(recordedClauses).map((text) => ({ text, kind: "record" as const })),
  ];
  return derived.length > 0 ? derived : null;
}

export function deriveAssumptions(raw: string[] | null | undefined): Assumption[] | null {
  const trimmed = (raw ?? []).map((s) => s.trim()).filter((s) => s.length > 0);
  return trimmed.length > 0
    ? trimmed.map((text, i) => ({ id: `asm-${i}`, text, status: "open" as const }))
    : null;
}

async function createWorkspace(
  title: string,
  description: string,
  tasks?: TaskProposal[],
  dependencies?: TaskDependency[],
  requirements?: Requirement[] | null,
  acceptanceClauses?: string[],
): Promise<{ task_id: string; root_task_ids: string[] }> {
  const hasTasks = tasks !== undefined && tasks.length > 0;
  const derived = deriveRequirementsFromAcceptanceClauses(acceptanceClauses);
  const reqs = requirements === undefined ? derived : requirements;
  const body: Record<string, unknown> = {
    title,
    description,
    ...(hasTasks ? { tasks, dependencies: dependencies ?? [] } : {}),
    ...(reqs == null ? {} : { requirements: reqs }),
  };
  return unwrap(
    await apiClient.POST(WORKSPACES_PATH, { body: body as components["schemas"]["CreateWorkspaceBody"] }),
  );
}

async function listWorkspaces(opts?: {
  bucket?: "archived";
  includeTriage?: boolean;
}): Promise<WorkspaceSummary[]> {
  return unwrap(
    await apiClient.GET(WORKSPACES_PATH, {
      params: {
        query: { bucket: opts?.bucket ?? null, include: opts?.includeTriage ? "triage" : null },
      },
    }),
  );
}

async function getWorkspace(taskId: string, opts?: { includeTriage?: boolean }): Promise<WorkspaceDetail> {
  assertSafeUrlPath(`${WORKSPACES_PATH}/${encodeURIComponent(taskId)}`);
  return unwrap(
    await apiClient.GET("/api/workspaces/{task_id}", {
      params: {
        path: { task_id: taskId },
        query: { include: opts?.includeTriage ? "triage" : null },
      },
    }),
  );
}

async function deleteWorkspace(taskId: string): Promise<void> {
  unwrap(await apiClient.DELETE("/api/workspaces/{task_id}", taskParams(taskId)));
}

async function archiveWorkspace(taskId: string): Promise<void> {
  unwrap(await apiClient.POST("/api/workspaces/{task_id}/archive", taskParams(taskId)));
}

async function unarchiveWorkspace(taskId: string): Promise<void> {
  unwrap(await apiClient.POST("/api/workspaces/{task_id}/unarchive", taskParams(taskId)));
}

async function stopWorkspace(taskId: string): Promise<void> {
  unwrap(await apiClient.POST("/api/workspaces/{task_id}/stop", taskParams(taskId)));
}

async function resumeWorkspace(taskId: string): Promise<void> {
  unwrap(await apiClient.POST("/api/workspaces/{task_id}/resume", taskParams(taskId)));
}

async function updateWorkspaceNote(
  taskId: string,
  note: string | null,
): Promise<{ note: string | null }> {
  return unwrap(
    await apiClient.PUT("/api/workspaces/{task_id}/note", { ...taskParams(taskId), body: { note } }),
  );
}

export interface DispatchPayload {
  instructions?: string | null | undefined;
  counter_case?: CounterCase | undefined;
}

function buildDispatchBody(team: string, payload: DispatchPayload): Record<string, unknown> {
  const body: Record<string, unknown> = { team };
  if (payload.instructions !== undefined && payload.instructions !== null) {
    const trimmed = payload.instructions.trim();
    if (trimmed.length > 0) body.instructions = trimmed;
  }
  if (payload.counter_case !== undefined) body.counter_case = payload.counter_case;
  return body;
}

async function dispatchTask(
  workspaceId: string,
  taskId: string,
  team: string,
  payload: DispatchPayload = {},
): Promise<{ job_id: number; invocation_id: string }> {
  assertSafeSegment(TASK_ID_RE, workspaceId, "task_id");
  assertSafeSegment(TASK_ID_RE, taskId, "task_id");
  return unwrap(
    await apiClient.POST("/api/workspaces/{workspace_id}/tasks/{task_id}/dispatch", {
      params: { path: { workspace_id: workspaceId, task_id: taskId } },
      body: buildDispatchBody(team, payload) as components["schemas"]["DispatchBody"],
    }),
  );
}

// Atomic create-and-dispatch (F3, 2026-07-21 dispatch-flow speed audit): fuses
// create_workspace + dispatch_task_team into the single POST /api/workspaces
// call the backend now supports via the optional `team` field on
// CreateWorkspaceBody — one HTTP round trip, one scan_predispatch call, and no
// orphan window between create and dispatch.
function applyEngineKey(body: Record<string, unknown>, engineKey: string | null | undefined): void {
  if (engineKey !== undefined && engineKey !== null) {
    body.engine_key = engineKey;
  }
}

function applyProjectId(body: Record<string, unknown>, projectId: string | null | undefined): void {
  if (projectId !== undefined && projectId !== null) {
    body.project_id = projectId;
  }
}

function applyRoadmapItemId(body: Record<string, unknown>, roadmapItemId: string | null | undefined): void {
  if (roadmapItemId !== undefined && roadmapItemId !== null && roadmapItemId.trim() !== "") {
    body.roadmap_item_id = roadmapItemId;
  }
}

export type PrepChatKind = components["schemas"]["PrepChatKind"];

function applyPrepChat(
  body: Record<string, unknown>,
  prepChatKind: PrepChatKind | null | undefined,
  prepChatSessionId: string | null | undefined,
): void {
  if (prepChatKind == null || prepChatSessionId == null) return;
  body.prep_chat_kind = prepChatKind;
  body.prep_chat_session_id = prepChatSessionId;
}

export interface CreateAndDispatchWorkspaceOptions {
  acceptanceClauses?: string[] | undefined;
  recordedClauses?: string[] | undefined;
  projectId?: string | null | undefined;
  roadmapItemId?: string | null | undefined;
  engineKey?: string | null | undefined;
  prepChatKind?: PrepChatKind | null | undefined;
  prepChatSessionId?: string | null | undefined;
}

async function createAndDispatchWorkspace(
  title: string,
  description: string,
  team: string,
  payload: DispatchPayload = {},
  options: CreateAndDispatchWorkspaceOptions = {},
): Promise<{ task_id: string; root_task_ids: string[] }> {
  const {
    acceptanceClauses,
    recordedClauses,
    projectId,
    roadmapItemId,
    engineKey,
    prepChatKind,
    prepChatSessionId,
  } = options;
  const body = buildDispatchBody(team, payload);
  body.title = title;
  body.description = description;
  const requirements = deriveRequirementsFromAcceptanceClauses(acceptanceClauses, recordedClauses);
  if (requirements !== null) {
    body.requirements = requirements;
  }
  applyProjectId(body, projectId);
  applyRoadmapItemId(body, roadmapItemId);
  applyEngineKey(body, engineKey);
  applyPrepChat(body, prepChatKind, prepChatSessionId);
  return unwrap(
    await apiClient.POST(WORKSPACES_PATH, { body: body as components["schemas"]["CreateWorkspaceBody"] }),
  );
}

async function getHandovers(taskId: string): Promise<Handover[]> {
  return unwrap(await apiClient.GET("/api/workspaces/{task_id}/handovers", taskParams(taskId)));
}

async function getDelivery(taskId: string): Promise<WorkspaceDelivery> {
  return unwrap(await apiClient.GET("/api/workspaces/{task_id}/delivery", taskParams(taskId)));
}

export type PrepChatBrief = components["schemas"]["ChatBrief"];
export type PrepChatTranscript = components["schemas"]["PrepChatTranscriptOut"];

async function getWorkspacePrepChat(taskId: string): Promise<PrepChatTranscript> {
  return unwrap(await apiClient.GET("/api/workspaces/{task_id}/prep-chat", taskParams(taskId)));
}

async function listInvocations(taskId: string): Promise<string[]> {
  return unwrap(await apiClient.GET("/api/workspaces/{task_id}/invocations", taskParams(taskId)));
}

async function getInvocation(
  taskId: string,
  invId: string,
): Promise<WorkflowDoc> {
  return unwrap(
    await apiClient.GET("/api/workspaces/{task_id}/invocations/{inv_id}", {
      params: { path: invocationPath(taskId, invId) },
    }),
  );
}

async function getDispatchInstructions(taskId: string): Promise<DispatchInstructionsMap> {
  return unwrap(
    await apiClient.GET("/api/workspaces/{task_id}/dispatch-instructions", taskParams(taskId)),
  );
}

async function getStickyInstructions(taskId: string): Promise<StickyInstructionOut> {
  return unwrap(
    await apiClient.GET("/api/workspaces/{task_id}/sticky-instructions", taskParams(taskId)),
  );
}

async function clearStickyInstructions(taskId: string): Promise<void> {
  unwrap(await apiClient.DELETE("/api/workspaces/{task_id}/sticky-instructions", taskParams(taskId)));
}

async function getAgentStream(
  taskId: string,
  invId: string,
  role: string,
): Promise<StreamEvent[]> {
  const path = invocationPath(taskId, invId);
  assertSafeSegment(ROLE_RE, role, "role");
  return unwrap(
    await apiClient.GET("/api/workspaces/{task_id}/invocations/{inv_id}/agents/{role}/stream", {
      params: { path: { ...path, role } },
    }),
  );
}

async function fetchNodeResult(
  taskId: string,
  invId: string,
  nodeId: string,
): Promise<NodeResultDoc> {
  const path = invocationPath(taskId, invId);
  assertSafeSegment(NODE_ID_RE, nodeId, "node_id");
  return unwrap(
    await apiClient.GET("/api/workspaces/{task_id}/invocations/{inv_id}/agents/{node_id}/result", {
      params: { path: { ...path, node_id: nodeId } },
    }),
  );
}

async function fetchNodeConfig(
  taskId: string,
  invId: string,
  nodeId: string,
): Promise<NodeConfigDoc> {
  const path = invocationPath(taskId, invId);
  assertSafeSegment(NODE_ID_RE, nodeId, "node_id");
  return unwrap(
    await apiClient.GET("/api/workspaces/{task_id}/invocations/{inv_id}/agents/{node_id}/config", {
      params: { path: { ...path, node_id: nodeId } },
    }),
  );
}

async function runSseSubscription(
  buildPath: () => string,
  controller: AbortController,
  handlers: { onEvent: (event: ParsedSseEvent) => void; onError: (detail: string) => void },
): Promise<void> {
  try {
    const safeUrl = sanitizeApiUrl(buildPath());
    const res = await fetch(safeUrl, { signal: controller.signal });
    if (!res.ok) {
      const detail = await readErrorDetail(res);
      handlers.onError(`stream open failed (${res.status}): ${detail}`);
      return;
    }
    if (!res.body) {
      handlers.onError("response body missing");
      return;
    }
    await pumpSseStream(res.body, handlers.onEvent);
  } catch (err: unknown) {
    if (controller.signal.aborted) return;
    handlers.onError(errorMessageOf(err));
  }
}

function subscribeWorkspace(
  taskId: string,
  handlers: WorkspaceEventHandlers,
): () => void {
  const controller = new AbortController();
  void runSseSubscription(
    () => {
      assertSafeSegment(TASK_ID_RE, taskId, "task_id");
      return `${WORKSPACES_PATH}/${encodeURIComponent(taskId)}/events`;
    },
    controller,
    handlers,
  );
  return () => controller.abort();
}

export type ChangeKind = "workspace_list" | "health" | "engines";

export const CHANGE_KINDS: readonly ChangeKind[] = ["workspace_list", "health", "engines"];

function parseChangedKinds(data: string): ChangeKind[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    return [];
  }
  if (parsed === null || typeof parsed !== "object") return [];
  const kinds = (parsed as { kinds?: unknown }).kinds;
  if (!Array.isArray(kinds)) return [];
  return CHANGE_KINDS.filter((k) => (kinds as unknown[]).includes(k));
}

export interface ServerEventsHandlers {
  onChanged: (kinds: ChangeKind[]) => void;
  onDisconnect: () => void;
}

function subscribeEvents(handlers: ServerEventsHandlers): () => void {
  const controller = new AbortController();
  let disconnected = false;
  const notifyDisconnect = () => {
    if (disconnected || controller.signal.aborted) return;
    disconnected = true;
    handlers.onDisconnect();
  };
  void runSseSubscription(
    () => "/api/events",
    controller,
    {
      onEvent: (event) => {
        if (event.event !== "changed") return;
        const kinds = parseChangedKinds(event.data);
        if (kinds.length > 0) handlers.onChanged(kinds);
      },
      onError: notifyDisconnect,
    },
  ).then(notifyDisconnect);
  return () => controller.abort();
}

export type ChatMessage = components["schemas"]["ChatMessageOut"];
export type ChatAck = components["schemas"]["ChatAck"];
export type PrepChatRead = components["schemas"]["PrepChatRead"];

export type ChatSseEventKind = "assistant_done" | "error" | "chat_event" | "turn_stopped";

export const CHAT_SSE_EVENT_KINDS: readonly ChatSseEventKind[] = [
  "assistant_done",
  "error",
  "chat_event",
  "turn_stopped",
];

type ScopeGapFinding = components["schemas"]["ScopeGapFinding"];

export interface TaskProposal {
  id?: string | null;
  title: string;
  description: string;
  team: string;
  instructions: string | null;
  requirements?: Requirement[] | null;
  scope_audit: ScopeGapFinding[];
}

export type TaskDependency = components["schemas"]["TaskDependency"];

export interface ChatEventHandlers {
  onEvent: (event: ParsedSseEvent) => void;
  onError: (detail: string) => void;
}

export type CounterCase = components["schemas"]["CounterCase"];

export interface DispatchProposal {
  team: string;
  rationale: string;
  instructions: string | null;
  scope_audit: ScopeGapFinding[];
  counter_case: CounterCase;
  title?: string | null;
  acceptance_clauses?: string[];
  recorded_clauses?: string[];
}

export type DispatchProposalResponse =
  | { status: "ready"; proposal: DispatchProposal; collision: CollisionCheckResponse }
  | { status: "need_more_info"; questions: string[] };

export type ToolUseCount = components["schemas"]["ToolUseCount"];
export type RunSummary = components["schemas"]["RunSummary"];

type RawDispatchProposal =
  | components["schemas"]["DispatchProposalReady"]
  | components["schemas"]["DispatchProposalNeedsInfo"];

function normalizeDispatchProposalResponse(raw: RawDispatchProposal): DispatchProposalResponse {
  if (!("proposal" in raw)) return { status: "need_more_info", questions: raw.questions };
  return {
    status: "ready",
    proposal: {
      ...raw.proposal,
      instructions: raw.proposal.instructions ?? null,
      scope_audit: raw.proposal.scope_audit!,
    },
    collision: toCollisionCheckResponse(raw.collision!),
  };
}

export class ArchivedSessionRedirect extends Error {
  readonly archivedTaskId: string;

  constructor(archivedTaskId: string) {
    super(`session archived to workspace ${archivedTaskId}`);
    this.archivedTaskId = archivedTaskId;
  }
}

const ARCHIVED_PREP_CHAT_RE = /\/api\/workspaces\/([^/]+)\/prep-chat$/;

function archivedTaskIdFromRedirect(res: Response): string | null {
  if (!res.redirected) return null;
  const match = ARCHIVED_PREP_CHAT_RE.exec(new URL(res.url).pathname);
  if (match === null) return null;
  /* v8 ignore next */
  return match[1] ?? null;
}

function chatSessionParams(sessionId: string) {
  assertSafeSegment(SESSION_ID_RE, sessionId, "session_id");
  return { params: { path: { session_id: sessionId } } };
}

function unwrapChatSession<T>(result: ClientResult<T>): T {
  const archivedTaskId = archivedTaskIdFromRedirect(result.response);
  if (archivedTaskId !== null) throw new ArchivedSessionRedirect(archivedTaskId);
  return unwrap(result);
}

const BUILD_CHAT_PATH = "/api/build-chat/sessions";
const EXPLORE_CHAT_PATH = "/api/explore-chat/sessions";
const EXPLORE_CONSULTANTS_PATH = "/api/explore-chat/consultants";

const BUILD_CHAT_PATHS = {
  session: "/api/build-chat/sessions/{session_id}",
  messages: "/api/build-chat/sessions/{session_id}/messages",
  interrupt: "/api/build-chat/sessions/{session_id}/interrupt",
  summarize: "/api/build-chat/sessions/{session_id}/summarize",
  brief: "/api/build-chat/sessions/{session_id}/brief",
  propose: "/api/build-chat/sessions/{session_id}/propose",
} as const;

const EXPLORE_CHAT_PATHS = {
  session: "/api/explore-chat/sessions/{session_id}",
  messages: "/api/explore-chat/sessions/{session_id}/messages",
  interrupt: "/api/explore-chat/sessions/{session_id}/interrupt",
  summarize: "/api/explore-chat/sessions/{session_id}/summarize",
  brief: "/api/explore-chat/sessions/{session_id}/brief",
  propose: "/api/explore-chat/sessions/{session_id}/propose",
} as const;

function makeChatApi(basePath: string, paths: typeof BUILD_CHAT_PATHS | typeof EXPLORE_CHAT_PATHS) {
  return {
    del: async (sessionId: string): Promise<void> => {
      unwrap(await apiClient.DELETE(paths.session, chatSessionParams(sessionId)));
    },
    send: async (sessionId: string, content: string) =>
      unwrap(
        await apiClient.POST(paths.messages, { ...chatSessionParams(sessionId), body: { content } }),
      ),
    interrupt: async (sessionId: string): Promise<void> => {
      unwrap(await apiClient.POST(paths.interrupt, chatSessionParams(sessionId)));
    },
    summarize: async (sessionId: string) =>
      unwrap(await apiClient.POST(paths.summarize, chatSessionParams(sessionId))),
    putBrief: async (sessionId: string, content: string) =>
      unwrap(
        await apiClient.PUT(paths.brief, { ...chatSessionParams(sessionId), body: { content } }),
      ),
    getBrief: async (sessionId: string) =>
      unwrap(await apiClient.GET(paths.brief, chatSessionParams(sessionId))),
    subscribe: (sessionId: string, handlers: ChatEventHandlers) => {
      const controller = new AbortController();
      void runSseSubscription(
        () => {
          assertSafeSegment(SESSION_ID_RE, sessionId, "session_id");
          return `${basePath}/${encodeURIComponent(sessionId)}/events`;
        },
        controller,
        handlers,
      );
      return () => controller.abort();
    },
  };
}

export type ChatBrief = components["schemas"]["ChatBrief"];
export type ChatInFlightTurn = components["schemas"]["ChatInFlightTurn"];
export type BuildChatSessionState = components["schemas"]["BuildChatSessionResponse"];
export type BuildChatSessionSummary = components["schemas"]["BuildChatSessionSummaryOut"];

const buildChatApi = {
  ...makeChatApi(BUILD_CHAT_PATH, BUILD_CHAT_PATHS),
  get: async (sessionId: string) =>
    unwrapChatSession(
      await apiClient.GET(BUILD_CHAT_PATHS.session, chatSessionParams(sessionId)),
    ),
};

// engine_key is required by the backend at create time (a conversation is bound to
// exactly one repository, fixed at creation) — every caller must resolve and supply
// it explicitly, so an unresolved repository can never silently bind to a default.
async function createBuildChatSession(
  engineKey: string,
  projectId?: string,
): Promise<{ session_id: string }> {
  const body: components["schemas"]["CreateBuildChatBody"] = { engine_key: engineKey };
  if (projectId !== undefined) body.project_id = projectId;
  return unwrap(await apiClient.POST(BUILD_CHAT_PATH, { body }));
}

async function listBuildChatSessions(
  projectId?: string,
  engineKey?: string,
): Promise<BuildChatSessionSummary[]> {
  return unwrap(
    await apiClient.GET(BUILD_CHAT_PATH, {
      params: { query: { project_id: projectId ?? null, engine_key: engineKey ?? null } },
    }),
  );
}

async function proposeBuildDispatch(sessionId: string): Promise<DispatchProposalResponse> {
  return normalizeDispatchProposalResponse(
    unwrap(await apiClient.POST(BUILD_CHAT_PATHS.propose, chatSessionParams(sessionId))),
  );
}

export type RepoProvisionStatus = components["schemas"]["RepoProvisionStatus"];
export type ExploreChatSessionState = components["schemas"]["ExploreChatSessionResponse"];
export type ConsultantInfo = components["schemas"]["ConsultantOut"];
export type ExploreChatSessionSummary = components["schemas"]["ExploreChatSessionSummaryOut"];

const exploreChatApi = {
  ...makeChatApi(EXPLORE_CHAT_PATH, EXPLORE_CHAT_PATHS),
  get: async (sessionId: string) =>
    unwrapChatSession(
      await apiClient.GET(EXPLORE_CHAT_PATHS.session, chatSessionParams(sessionId)),
    ),
};

async function listExploreChatSessions(
  projectId?: string,
  engineKey?: string,
): Promise<ExploreChatSessionSummary[]> {
  return unwrap(
    await apiClient.GET(EXPLORE_CHAT_PATH, {
      params: { query: { project_id: projectId ?? null, engine_key: engineKey ?? null } },
    }),
  );
}

async function createExploreChatSession(
  consultantKey: string,
  engineKey: string,
  projectId?: string,
): Promise<{ session_id: string }> {
  const body: components["schemas"]["CreateExploreChatBody"] = {
    consultant_key: consultantKey,
    engine_key: engineKey,
  };
  if (projectId !== undefined) body.project_id = projectId;
  return unwrap(await apiClient.POST(EXPLORE_CHAT_PATH, { body }));
}

async function fetchConsultants(): Promise<ConsultantInfo[]> {
  return unwrap(await apiClient.GET(EXPLORE_CONSULTANTS_PATH));
}

async function proposeExploreDispatch(sessionId: string): Promise<DispatchProposalResponse> {
  return normalizeDispatchProposalResponse(
    unwrap(await apiClient.POST(EXPLORE_CHAT_PATHS.propose, chatSessionParams(sessionId))),
  );
}

export function deriveBuildTitle(content: string): string {
  const firstLine = content
    .split("\n")
    .map((l) => l.trim())
    .find((l) => l.length > 0);
  const stripped = (firstLine ?? "").replace(/^#+\s*/, "").slice(0, 50);
  return stripped.length > 0 ? stripped : "build";
}

export function composeClarifications(
  briefContent: string,
  answered: { question: string; answer: string }[],
): string {
  const blocks = answered
    .map((qa) => `### ${qa.question}\n${qa.answer.trim()}`)
    .join("\n\n");
  return `${briefContent}\n\n## Clarifications\n\n${blocks}`;
}

export interface CollisionMatchInfo {
  label: string;
  kind: "pr" | "workspace";
  deep_link: string;
}

export interface CollisionCheckResponse {
  state: "green" | "yellow" | "red";
  sentence: string;
  match: CollisionMatchInfo | null;
}

export type ChatFamily = NonNullable<components["schemas"]["CollisionCheckBody"]["chat_family"]>;

export interface CheckCollisionOptions {
  chatFamily?: ChatFamily | undefined;
  sessionId?: string | undefined;
}

function toCollisionCheckResponse(
  verdict: components["schemas"]["CollisionVerdictOut"],
): CollisionCheckResponse {
  return { state: verdict.state, sentence: verdict.sentence, match: verdict.match ?? null };
}

async function checkCollision(
  brief: string,
  opts: CheckCollisionOptions = {},
): Promise<CollisionCheckResponse> {
  const body: components["schemas"]["CollisionCheckBody"] = { brief };
  if (opts.chatFamily !== undefined) body.chat_family = opts.chatFamily;
  if (opts.sessionId !== undefined) body.session_id = opts.sessionId;
  return toCollisionCheckResponse(unwrap(await apiClient.POST("/api/collision-check", { body })));
}

export type EngineRepoIdentity = components["schemas"]["RepoIdentity"];
export type EnginePosture = components["schemas"]["Posture"];
export type AccentChoice = components["schemas"]["AccentChoice"];
export type AccentColor = NonNullable<AccentChoice["color"]>;
export type AccentIntensity = AccentChoice["intensity"];

export const ACCENT_COLORS: readonly AccentColor[] = [
  "iris",
  "violet",
  "plum",
  "orchid",
  "magenta",
  "rose",
  "ruby",
  "crimson",
  "orange",
  "amber",
  "tan",
  "green",
  "teal",
  "blue",
  "slate",
  "bone",
];

export const ACCENT_INTENSITIES: readonly AccentIntensity[] = ["soft", "standard", "bold"];

export type EngineConfig = components["schemas"]["EngineConfig"];
export type EngineSummary = Pick<EngineConfig, "repo" | "posture" | "hidden" | "accent" | "forge">;
export type EnginePoolState = components["schemas"]["PoolStateResponse"];
export type EnginePreflightFinding = components["schemas"]["Finding"];
export type PreflightStatus = EnginePreflightFinding["status"];
export type EnginePreflightResult = components["schemas"]["PreflightResult"];
export type EngineSource = components["schemas"]["EngineSource"];
export type EngineQuiesceReport = components["schemas"]["QuiesceReport"];
export type EngineCodeQualityConfig = components["schemas"]["CodeQualityConfig"];
export type EngineRoleConfig = components["schemas"]["RoleConfig"];
export type EngineForgeConfig = components["schemas"]["ForgeConfig"];
export type EngineCiConfig = components["schemas"]["CiConfig"];
export type EngineFlowGraphPolicy = components["schemas"]["FlowGraphPolicy"];
export type EngineDependencySweepConfig = components["schemas"]["DependencySweepConfig"];
export type EnginePresetInfo = components["schemas"]["EnginePresetInfo"];
export type AgentCatalogue = components["schemas"]["AgentCatalogue"];
export type AgentCatalogueSummary = components["schemas"]["AgentCatalogueSummary"];
export type AgentEntry = components["schemas"]["AgentEntry"];
export type AgentKind = AgentEntry["kind"];
export type AgentFlag = components["schemas"]["FlagOut"];
export type AgentPromptSegment = components["schemas"]["PromptSegmentOut"];
export type AgentValue = components["schemas"]["ValueOut"];
export type LaunchRecord = components["schemas"]["LaunchRecord"];
export type DependencySweepStatus = components["schemas"]["DependencySweepStatusResponse"];
export type OnboardingState = components["schemas"]["OnboardingState"];
export type OnboardingReadiness = components["schemas"]["OnboardingReadiness"];

async function listEngines(): Promise<EngineSummary[]> {
  return unwrap(await apiClient.GET("/api/engines")).engines;
}

export const ENGINE_SEGMENT_RE = /^[A-Za-z0-9._-]{1,100}$/;

function engineOwnerRepo(engineKey: string): { owner: string; repo: string } {
  const idx = engineKey.indexOf("/");
  const owner = idx === -1 ? "" : engineKey.slice(0, idx);
  const repo = idx === -1 ? "" : engineKey.slice(idx + 1);
  assertSafeSegment(ENGINE_SEGMENT_RE, owner, "engine_owner");
  assertSafeSegment(ENGINE_SEGMENT_RE, repo, "engine_repo");
  return { owner, repo };
}

function enginePath(engineKey: string, suffix = ""): string {
  const { owner, repo } = engineOwnerRepo(engineKey);
  return `/api/engines/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}${suffix}`;
}

async function getEngine(engineKey: string): Promise<EngineConfig> {
  return unwrap(await apiClient.GET("/api/engines/{owner}/{repo}", engineParams(engineKey)));
}

async function getEngineSources(): Promise<EngineSource[]> {
  return unwrap(await apiClient.GET("/api/engines/sources")).sources;
}

async function getEnginePool(engineKey: string): Promise<EnginePoolState> {
  return unwrap(await apiClient.GET("/api/engines/{owner}/{repo}/pool", engineParams(engineKey)));
}

async function getEnginePreflight(engineKey: string): Promise<EnginePreflightResult | null> {
  return unwrap(
    await apiClient.GET("/api/engines/{owner}/{repo}/preflight", engineParams(engineKey)),
  );
}

export type RepoStatusProvenance = components["schemas"]["Provenance"];
export type RepoStatusProvenanceStatus = RepoStatusProvenance["status"];
export type RepoStatusMainCi = components["schemas"]["MainCiField"];
export type RepoStatusOpenPrs = components["schemas"]["OpenPrsField"];
export type WorkspaceSizeClass = components["schemas"]["SizeClass"];
export type RepoStatusCost = components["schemas"]["CostField"];
export type RepoStatusSizeStat = components["schemas"]["SizeStat"];
export type RepoStatusDeliveryTime = components["schemas"]["DeliveryTimeField"];
export type RepoStatusLastDeploy = components["schemas"]["LastDeployField"];
export type RepoStatusDeployOutcome = NonNullable<RepoStatusLastDeploy["outcome"]>;
export type RepoStatusHealth = components["schemas"]["HealthField"];
export type RepoStatusHealthVerdict = NonNullable<RepoStatusHealth["verdict"]>;
export type RepoStatus = components["schemas"]["RepoStatus"];

async function getRepoStatus(engineKey: string): Promise<RepoStatus> {
  return unwrap(await apiClient.GET("/api/engines/{owner}/{repo}/status", engineParams(engineKey)));
}

export type RecurringCadence = components["schemas"]["Cadence"];
export type RecurringCadenceKind = RecurringCadence["kind"];
export type RecurringOverdue = components["schemas"]["Overdue"];
export type RecurringOverdueState = RecurringOverdue["state"];
export type RecurringLastRun = components["schemas"]["LastRun"];
export type RecurringRunState = RecurringLastRun["state"];
export type RecurringProcess = components["schemas"]["RecurringProcess"];
export type RecurringProcessFamily = RecurringProcess["family"];
export type RecurringDurationState = RecurringProcess["duration_state"];
export type RecurringBackupCoverage = components["schemas"]["BackupCoverage"];
export type RecurringStatus = components["schemas"]["RecurringStatus"];

async function getRecurringStatus(engineKey: string): Promise<RecurringStatus> {
  return unwrap(
    await apiClient.GET("/api/engines/{owner}/{repo}/recurring-status", engineParams(engineKey)),
  );
}

async function setEnginePosture(engineKey: string, posture: EnginePosture): Promise<EngineConfig> {
  return unwrap(
    await apiClient.POST("/api/engines/{owner}/{repo}/posture", {
      ...engineParams(engineKey),
      body: { posture, confirm: true },
    }),
  );
}

async function setEngineAccent(
  engineKey: string,
  accent: AccentChoice | null,
): Promise<EngineConfig> {
  return unwrap(
    await apiClient.PUT("/api/engines/{owner}/{repo}/accent", {
      ...engineParams(engineKey),
      body: { accent },
    }),
  );
}

async function hideEngine(engineKey: string): Promise<EngineQuiesceReport> {
  return unwrap(await apiClient.POST("/api/engines/{owner}/{repo}/hide", engineParams(engineKey)));
}

async function unhideEngine(engineKey: string): Promise<EngineConfig> {
  return unwrap(await apiClient.POST("/api/engines/{owner}/{repo}/unhide", engineParams(engineKey)));
}

async function getEngineConfig(engineKey: string): Promise<EngineConfig> {
  return unwrap(await apiClient.GET("/api/engines/{owner}/{repo}", engineParams(engineKey)));
}

async function createEngine(config: EngineConfig): Promise<EngineConfig> {
  return unwrap(await apiClient.POST("/api/engines", { body: config }));
}

async function updateEngineConfig(
  engineKey: string,
  config: EngineConfig,
): Promise<EngineConfig> {
  return unwrap(
    await apiClient.PATCH("/api/engines/{owner}/{repo}", {
      ...engineParams(engineKey),
      body: config,
    }),
  );
}

async function listEnginePresets(): Promise<EnginePresetInfo[]> {
  return unwrap(await apiClient.GET("/api/engine-presets")).presets;
}

async function getEnginePresetConfig(
  preset: string,
  owner: string,
  repo: string,
): Promise<EngineConfig> {
  assertSafeUrlPath(`/api/engine-presets/${encodeURIComponent(preset)}/config`);
  return unwrap(
    await apiClient.GET("/api/engine-presets/{name}/config", {
      params: { path: { name: preset }, query: { owner, repo } },
    }),
  );
}

export type EngineConfigFindingScope = "roster" | "general";

export interface EngineConfigFinding {
  scope: EngineConfigFindingScope;
  message: string;
}

const ROSTER_FINDING_RE = /\b(team|graph|cycle)\b/i;

export function classifyEngineConfigFinding(message: string): EngineConfigFinding {
  return { scope: ROSTER_FINDING_RE.test(message) ? "roster" : "general", message };
}

async function getEngineAgents(engineKey: string): Promise<AgentCatalogue> {
  return unwrap(await apiClient.GET("/api/engines/{owner}/{repo}/agents", engineParams(engineKey)));
}

async function getEngineAgentsSummary(engineKey: string): Promise<AgentCatalogueSummary> {
  return unwrap(
    await apiClient.GET("/api/engines/{owner}/{repo}/agents/summary", engineParams(engineKey)),
  );
}

async function runEnginePreflight(engineKey: string): Promise<EnginePreflightResult> {
  return unwrap(
    await apiClient.POST("/api/engines/{owner}/{repo}/preflight", engineParams(engineKey)),
  );
}

async function getEngineDependencySweep(engineKey: string): Promise<DependencySweepStatus> {
  return unwrap(
    await apiClient.GET("/api/engines/{owner}/{repo}/dependency-sweep", engineParams(engineKey)),
  );
}

// The toggle's 422 response body is a Finding (check/status/message/remedy), not the
// plain-string `detail` every other route error carries — the shared error mapping
// would flatten it into an unreadable JSON blob, so this class lets the tile recover the
// structured finding instead of a string-only error message.
export class DependencySweepBlockedError extends Error {
  readonly finding: EnginePreflightFinding;

  constructor(finding: EnginePreflightFinding) {
    super(finding.message);
    this.finding = finding;
  }
}

function parseJsonBody(body: string): unknown {
  try {
    return JSON.parse(body);
  } catch {
    return null;
  }
}

const FINDING_STATUSES: ReadonlySet<unknown> = new Set(["pass", "fail", "unknown"]);

function isFindingShape(value: unknown): value is EnginePreflightFinding {
  if (value === null || typeof value !== "object") return false;
  const d = value as Record<string, unknown>;
  return (
    typeof d.check === "string" &&
    FINDING_STATUSES.has(d.status) &&
    typeof d.message === "string" &&
    typeof d.remedy === "string"
  );
}

function findingFromBody(body: string): EnginePreflightFinding | null {
  const parsed = parseJsonBody(body);
  if (parsed === null || typeof parsed !== "object") return null;
  const detail = (parsed as { detail?: unknown }).detail;
  return isFindingShape(detail) ? detail : null;
}

async function setEngineDependencySweepEnabled(
  engineKey: string,
  enabled: boolean,
): Promise<EngineConfig> {
  const headers = new Headers();
  headers.set(JSON_CONTENT_TYPE, JSON_TYPE);
  const token = readControlToken();
  if (token) headers.set(CONTROL_TOKEN_HEADER, token);
  const res = await fetch(sanitizeApiUrl(enginePath(engineKey, "/dependency-sweep")), {
    method: "POST",
    headers,
    body: JSON.stringify({ enabled }),
  });
  if (res.ok) return (await res.json()) as EngineConfig;
  const body = await res.text();
  if (res.status === 422) {
    const finding = findingFromBody(body);
    if (finding !== null) throw new DependencySweepBlockedError(finding);
  }
  throw new Error(`${res.status}: ${detailFromBody(body) || res.statusText}`);
}

export type ConfigProposalBasePreset = "dotnet" | "ios" | "node" | "python";

export const CONFIG_PROPOSAL_BASE_PRESETS: readonly ConfigProposalBasePreset[] = [
  "dotnet",
  "ios",
  "node",
  "python",
];

export type ProposableEngineConfigField =
  | "gate_commands"
  | "warmup_commands"
  | "base_tools"
  | "team_tools"
  | "roles"
  | "transition_graph"
  | "flow_policy";

type ProposableFieldValues = Required<Pick<EngineConfig, ProposableEngineConfigField>>;

export type ProposalFieldDiff = {
  [F in ProposableEngineConfigField]: {
    field: F;
    baseline: ProposableFieldValues[F];
    proposed: ProposableFieldValues[F];
    changed: boolean;
  };
}[ProposableEngineConfigField];

export interface ConfigProposalResult {
  slug: string;
  base_preset: string;
  detected_stacks: string[];
  additional_stacks: string[];
  proposed_config: EngineConfig;
  diff: ProposalFieldDiff[];
  validation: EnginePreflightFinding;
}

// The 422 response body is a Finding (check="config_proposal") — mirrors
// DependencySweepBlockedError so the caller can recover the structured finding (and its
// remedy pointing at the manual preset-seed flow) instead of an unreadable JSON blob.
export class ConfigProposalBlockedError extends Error {
  readonly finding: EnginePreflightFinding;

  constructor(finding: EnginePreflightFinding) {
    super(finding.message);
    this.finding = finding;
  }
}

async function proposeEngineConfig(
  engineKey: string,
  basePreset: ConfigProposalBasePreset | null = null,
): Promise<ConfigProposalResult> {
  const headers = new Headers();
  headers.set(JSON_CONTENT_TYPE, JSON_TYPE);
  const token = readControlToken();
  if (token) headers.set(CONTROL_TOKEN_HEADER, token);
  const res = await fetch(sanitizeApiUrl(enginePath(engineKey, "/config-proposal")), {
    method: "POST",
    headers,
    body: JSON.stringify({ base_preset: basePreset }),
  });
  if (res.ok) return (await res.json()) as ConfigProposalResult;
  const body = await res.text();
  if (res.status === 422) {
    const finding = findingFromBody(body);
    if (finding !== null) throw new ConfigProposalBlockedError(finding);
  }
  throw new ApiError(res.status, detailFromBody(body) || res.statusText);
}

async function validateEngineConfigDraft(
  engineKey: string,
  draft: EngineConfig,
): Promise<EnginePreflightFinding> {
  return unwrap(
    await apiClient.POST("/api/engines/{owner}/{repo}/config-draft-validate", {
      ...engineParams(engineKey),
      body: draft,
    }),
  );
}

async function scaffoldEngine(engineKey: string): Promise<OnboardingState> {
  return unwrap(await apiClient.POST("/api/engines/{owner}/{repo}/scaffold", engineParams(engineKey)));
}

async function protectEngine(engineKey: string): Promise<OnboardingState> {
  return unwrap(await apiClient.POST("/api/engines/{owner}/{repo}/protect", engineParams(engineKey)));
}

async function getEngineOnboarding(engineKey: string): Promise<OnboardingReadiness> {
  return unwrap(
    await apiClient.GET("/api/engines/{owner}/{repo}/onboarding", engineParams(engineKey)),
  );
}

const SECRETS_PATH = "/api/secrets";

// There is deliberately no `value` field on this response shape (mirrors
// alan.routes.secrets.SecretRecordResponse) — the wizard and the Credentials
// section can never hold a submitted secret value in state, by construction.
export type SecretRecordResponse = components["schemas"]["SecretRecordResponse"];

async function listSecrets(): Promise<SecretRecordResponse[]> {
  return unwrap(await apiClient.GET(SECRETS_PATH)).secrets;
}

export type CreateSecretPayload = components["schemas"]["SecretCreateBody"];

async function createSecret(payload: CreateSecretPayload): Promise<SecretRecordResponse> {
  return unwrap(await apiClient.POST(SECRETS_PATH, { body: payload }));
}

export interface DeleteSecretPayload {
  engine: string;
  purpose: string;
}

// DELETE /api/secrets is control-token gated, body-based (no path segment
// carries the identity) and returns 204 with no body, so this is a bespoke
// fetch that explicitly attaches the header and parses no response body.
async function deleteSecret(payload: DeleteSecretPayload): Promise<void> {
  const headers = new Headers();
  headers.set(JSON_CONTENT_TYPE, JSON_TYPE);
  const token = readControlToken();
  if (token) headers.set(CONTROL_TOKEN_HEADER, token);
  const res = await fetch(sanitizeApiUrl(SECRETS_PATH), {
    method: "DELETE",
    headers,
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorDetail(res));
  }
}

// Mirrors alan.control_token_store.ControlTokenStatusState / ControlTokenStatusResponse.
export type ControlTokenLifecycleState = "active" | "expired" | "revoked";

interface ControlTokenStatusBody {
  state: ControlTokenLifecycleState;
  deadline: string | null;
}

export interface ControlTokenStatus extends ControlTokenStatusBody {
  // The response's own `Date` header, captured alongside the parsed body —
  // the only way to compute "time remaining" against the server's clock
  // without widening the wire contract (control-token-expiry.spec.ts pins
  // GET /status's body to exactly {state, deadline}). Null when the response
  // carried no `Date` header, e.g. a test double that doesn't synthesize one.
  serverNowMs: number | null;
}

const CONTROL_TOKEN_PATH = "/api/control-token";

function parseServerNowMs(res: Response): number | null {
  const raw = res.headers.get("Date");
  if (raw === null) return null;
  const parsed = Date.parse(raw);
  return Number.isNaN(parsed) ? null : parsed;
}

async function parseControlTokenResponse(res: Response): Promise<ControlTokenStatus> {
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorDetail(res));
  }
  const body = (await res.json()) as ControlTokenStatusBody;
  return { ...body, serverNowMs: parseServerNowMs(res) };
}

// Unauthenticated by design (alan.routes.control_token) — expiry status must
// be readable without a valid credential, since it is what tells the
// operator the credential is dead in the first place. Deliberately a bare
// fetch that never reads the token from localStorage: this GET needs none, and
// sending one anyway would only widen the credential's exposure in logs and
// proxies in between for no benefit.
async function getControlTokenStatus(): Promise<ControlTokenStatus> {
  const res = await fetch(sanitizeApiUrl(`${CONTROL_TOKEN_PATH}/status`), { method: "GET" });
  return parseControlTokenResponse(res);
}

async function authedControlTokenMutation(path: string): Promise<ControlTokenStatus> {
  const headers = new Headers();
  headers.set(JSON_CONTENT_TYPE, JSON_TYPE);
  const token = readControlToken();
  if (token) headers.set(CONTROL_TOKEN_HEADER, token);
  const res = await fetch(sanitizeApiUrl(path), { method: "POST", headers });
  return parseControlTokenResponse(res);
}

// Leaves the token value unchanged and transmits no secret — only pushes the
// server's expiry deadline forward.
async function extendControlToken(): Promise<ControlTokenStatus> {
  return authedControlTokenMutation(`${CONTROL_TOKEN_PATH}/extend`);
}

// Issues a brand-new token value server-side and invalidates the old one
// everywhere; the new value is never in the response body, by the same
// never-transmitted invariant as every other control-token endpoint.
async function rotateControlToken(): Promise<ControlTokenStatus> {
  return authedControlTokenMutation(`${CONTROL_TOKEN_PATH}/rotate`);
}

async function revokeControlToken(): Promise<ControlTokenStatus> {
  return authedControlTokenMutation(`${CONTROL_TOKEN_PATH}/revoke`);
}

export interface UpdateProjectPayload {
  title?: string;
  status?: ProjectStatus;
  doc_ref?: string | null;
  autonomy?: ProjectAutonomy;
  budget_usd?: number | null;
  engine_key?: string | null;
}

export interface CompleteProjectPayload {
  force?: boolean;
  reason?: string;
}

const PROJECTS_PATH = "/api/projects";
const PROJECT_ID_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const ROADMAP_ITEM_ID_RE = /^[A-Za-z0-9_-]{1,80}$/;

function projectParams(projectId: string) {
  assertSafeSegment(PROJECT_ID_RE, projectId, "project_id");
  return { params: { path: { project_id: projectId } } };
}

function roadmapItemParams(projectId: string, itemId: string) {
  assertSafeSegment(PROJECT_ID_RE, projectId, "project_id");
  assertSafeSegment(ROADMAP_ITEM_ID_RE, itemId, "item_id");
  return { params: { path: { project_id: projectId, item_id: itemId } } };
}

async function listProjects(opts?: { includeArchived?: boolean }): Promise<ProjectListEntry[]> {
  return unwrap(
    await apiClient.GET(PROJECTS_PATH, {
      params: { query: { include: opts?.includeArchived ? "archived" : null } },
    }),
  );
}

async function createProject(
  title: string,
  docRef: string | null,
  intendedEngineKey?: string | null,
): Promise<Project> {
  const body: components["schemas"]["CreateProjectBody"] = { title, doc_ref: docRef };
  if (intendedEngineKey != null) body.intended_engine_key = intendedEngineKey;
  return unwrap(await apiClient.POST(PROJECTS_PATH, { body }));
}

async function getProject(projectId: string): Promise<ProjectDetail> {
  return unwrap(await apiClient.GET("/api/projects/{project_id}", projectParams(projectId)));
}

async function updateProject(
  projectId: string,
  payload: UpdateProjectPayload,
): Promise<Project> {
  return unwrap(
    await apiClient.PATCH("/api/projects/{project_id}", {
      ...projectParams(projectId),
      body: payload,
    }),
  );
}

async function deleteProject(projectId: string): Promise<void> {
  unwrap(await apiClient.DELETE("/api/projects/{project_id}", projectParams(projectId)));
}

async function completeProject(
  projectId: string,
  payload: CompleteProjectPayload = {},
): Promise<Project> {
  return unwrap(
    await apiClient.POST("/api/projects/{project_id}/complete", {
      ...projectParams(projectId),
      body: { force: payload.force ?? false, reason: payload.reason ?? "" },
    }),
  );
}

async function getProjectDoc(projectId: string): Promise<ProjectDoc> {
  return unwrap(await apiClient.GET("/api/projects/{project_id}/doc", projectParams(projectId)));
}

async function proposeProjectAmendment(
  projectId: string,
  vision: string,
  scope: string,
  decisionEntry: string,
): Promise<ProjectAmendmentProposal> {
  return unwrap(
    await apiClient.POST("/api/projects/{project_id}/amendments", {
      ...projectParams(projectId),
      body: { vision, scope, decision_entry: decisionEntry },
    }),
  );
}

async function previewRoadmap(
  projectId: string,
  markdown: string,
): Promise<RoadmapPreviewResponse> {
  return unwrap(
    await apiClient.POST("/api/projects/{project_id}/roadmap/preview", {
      ...projectParams(projectId),
      body: { markdown },
    }),
  );
}

async function importRoadmap(
  projectId: string,
  markdown: string,
): Promise<RoadmapImportResponse> {
  return unwrap(
    await apiClient.POST("/api/projects/{project_id}/roadmap/import", {
      ...projectParams(projectId),
      body: { markdown },
    }),
  );
}

async function proposeRoadmapReorder(
  projectId: string,
  orderedIds: string[],
): Promise<ProjectAmendmentProposal> {
  return unwrap(
    await apiClient.POST("/api/projects/{project_id}/roadmap/reorder", {
      ...projectParams(projectId),
      body: { ordered_ids: orderedIds },
    }),
  );
}

async function proposeRoadmapEdit(
  projectId: string,
  itemId: string,
  payload: ProposeRoadmapEditPayload,
): Promise<ProjectAmendmentProposal> {
  return unwrap(
    await apiClient.POST("/api/projects/{project_id}/roadmap/items/{item_id}", {
      ...roadmapItemParams(projectId, itemId),
      body: payload,
    }),
  );
}

async function advanceProjectRoadmap(projectId: string): Promise<RoadmapAdvanceResult> {
  return unwrap(
    await apiClient.POST("/api/projects/{project_id}/roadmap/advance", projectParams(projectId)),
  );
}

async function getRoadmapNextItemProposal(
  projectId: string,
): Promise<RoadmapNextItemProposalResponse> {
  return unwrap(
    await apiClient.GET(
      "/api/projects/{project_id}/roadmap/next-item-proposal",
      projectParams(projectId),
    ),
  );
}

export const api = {
  getHealth,
  getHealthRepos,
  getHealthDigest,
  getUnblockerActivity,
  getDriverLoops,
  pauseDriverLoop,
  resumeDriverLoop,
  resumeQueue,
  runDriverLoopNow,
  listProjects,
  createProject,
  getProject,
  updateProject,
  deleteProject,
  completeProject,
  getProjectDoc,
  proposeProjectAmendment,
  previewRoadmap,
  importRoadmap,
  proposeRoadmapReorder,
  proposeRoadmapEdit,
  advanceProjectRoadmap,
  getRoadmapNextItemProposal,
  createWorkspace,
  createAndDispatchWorkspace,
  listWorkspaces,
  getWorkspace,
  deleteWorkspace,
  archiveWorkspace,
  unarchiveWorkspace,
  stopWorkspace,
  resumeWorkspace,
  updateWorkspaceNote,
  dispatchTask,
  listEngines,
  getEngine,
  getEngineSources,
  getEnginePool,
  getEnginePreflight,
  getRepoStatus,
  getRecurringStatus,
  setEnginePosture,
  setEngineAccent,
  hideEngine,
  unhideEngine,
  getEngineDependencySweep,
  setEngineDependencySweepEnabled,
  proposeEngineConfig,
  validateEngineConfigDraft,
  scaffoldEngine,
  protectEngine,
  getEngineOnboarding,
  getEngineConfig,
  createEngine,
  updateEngineConfig,
  listEnginePresets,
  getEnginePresetConfig,
  listSecrets,
  createSecret,
  deleteSecret,
  getControlTokenStatus,
  extendControlToken,
  rotateControlToken,
  revokeControlToken,
  getEngineAgents,
  getEngineAgentsSummary,
  runEnginePreflight,
  subscribeWorkspace,
  listInvocations,
  getInvocation,
  getDispatchInstructions,
  getStickyInstructions,
  clearStickyInstructions,
  getAgentStream,
  fetchNodeResult,
  fetchNodeConfig,
  getHandovers,
  getDelivery,
  getWorkspacePrepChat,
  createBuildChatSession,
  listBuildChatSessions,
  getBuildChatSession: buildChatApi.get,
  deleteBuildChatSession: buildChatApi.del,
  sendBuildChatMessage: buildChatApi.send,
  interruptBuildChatSession: buildChatApi.interrupt,
  summarizeBuildChat: buildChatApi.summarize,
  putBuildChatBrief: buildChatApi.putBrief,
  getBuildChatBrief: buildChatApi.getBrief,
  proposeBuildDispatch,
  subscribeBuildChatSession: buildChatApi.subscribe,
  createExploreChatSession,
  listExploreChatSessions,
  getExploreChatSession: exploreChatApi.get,
  deleteExploreChatSession: exploreChatApi.del,
  sendExploreChatMessage: exploreChatApi.send,
  interruptExploreChatSession: exploreChatApi.interrupt,
  summarizeExploreChat: exploreChatApi.summarize,
  putExploreChatBrief: exploreChatApi.putBrief,
  getExploreChatBrief: exploreChatApi.getBrief,
  proposeExploreDispatch,
  subscribeExploreChatSession: exploreChatApi.subscribe,
  fetchConsultants,
  checkCollision,
  subscribeEvents,
};
