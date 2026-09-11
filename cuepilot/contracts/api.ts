// Shared frontend/backend contract, v1. Backend owns these field names.
export type Scene = 'holding' | 'intro' | 'presentation';
export type RunStatus = 'queued' | 'needs_approval' | 'approved' | 'running' | 'completed' | 'blocked' | 'failed';
export interface Speaker { id: string; name: string; title: string; ready: boolean; presentationAssetId: string; }
export interface Asset { id: string; title: string; kind: 'slide'; status: 'ready' | 'missing'; }
export interface Show { id: string; revision: number; title: string; speakers: Speaker[]; assets: Asset[]; }
export interface Stage { revision: number; scene: Scene; speakerId: string | null; title: string; subtitle: string; assetId: string | null; reason: string | null; updatedAt: string; }
export interface Evidence { provider: string; status: 'verified' | 'blocked' | 'failed' | 'fixture'; operation: string; evidence: unknown; reason?: string | null; }
export interface Cue { index: number; scene: Scene; }
export interface Plan { id: string; hash: string; recipeId: string; recipeVersion: number; showRevision: number; speakerId: string; cues: Cue[]; origin: 'fixture' | 'sponsor'; }
export interface Run { id: string; showId: string; speakerId: string; executionMode: 'practice' | 'live'; status: RunStatus; notes: string; plan: Plan | null; nextStep: number; receipts: CueReceipt[]; traces: Evidence[]; reason: string | null; createdAt: string; updatedAt: string; }
export interface CueReceipt { ok: true; id: string; runId: string; stepIndex: number; scene: Scene; stageRevision: number; committedAt: string; }
export interface ApiError { detail: { code: string; message: string } }

// GET /api/v1/show -> Show; GET /api/v1/stage -> Stage
// GET /api/v1/runs -> Run[]; GET /api/v1/runs/:id -> Run
// POST /api/v1/runs {speakerId, executionMode:'practice'|'live', notes?} -> Run
// POST /api/v1/runs/:id/approve {planHash} -> Run
// POST /api/v1/runs/:id/advance {requestId} -> CueReceipt (practice only)
// POST /api/v1/runs/:id/execute {} -> Run (live, RocketRide -> Rote)
// PATCH /api/v1/assets/:id {status:'ready'|'missing',expectedRevision:number} -> Show
// PATCH /api/v1/speakers/:id {ready:boolean,expectedRevision:number} -> Show
// All mutations require operator Bearer token, injected by the dev-server proxy.
// Poll stage/show/current run every 500ms. Source of truth is the API, not timers.
