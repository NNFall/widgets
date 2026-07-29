export type BuilderEngine = 'direct' | 'antigravity';
export type BuilderRunStatus = 'created' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
export type BuilderStage =
  | 'art_direction'
  | 'foundation'
  | 'identity'
  | 'conversation'
  | 'motion_polish'
  | 'validation'
  | 'agent_build';

export interface TokenUsage {
  prompt_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
  total_tokens: number;
}

export interface BuilderRequest {
  engine: BuilderEngine;
  brief: string;
  reference_context: string;
  source_url: string;
  locale: string;
  creativity: number;
  viewport_targets: Array<'desktop' | 'mobile'>;
  max_repairs: number;
  contract_id: string;
  creative_profile: 'balanced' | 'product_chat' | 'brand_motion' | 'ai_character';
  visual_repair_limit: number;
}

export interface ValidationIssue {
  code: string;
  field: string;
  message: string;
}

export interface WidgetArtifact {
  id?: string;
  quality_status?: string;
  source?: string;
  schema_version: string;
  revision: number;
  stage: BuilderStage;
  art_direction: string;
  body_html: string;
  css: string;
  javascript: string;
  theme_tokens: Record<string, string>;
  suggested_actions: string[];
  change_summary: string;
  layout_contract: Record<string, string>;
}

export interface BuilderEvent {
  run_id: string;
  sequence: number;
  timestamp: string;
  type: string;
  stage: BuilderStage | null;
  status: string;
  message: string;
  revision: number | null;
  usage: TokenUsage;
  issues: ValidationIssue[];
  changes: string[];
  error_code: string | null;
}

export interface BuilderRunSnapshot {
  run_id: string;
  request: BuilderRequest;
  status: BuilderRunStatus;
  progress?: number;
  created_at: string;
  updated_at: string;
  latest_sequence: number;
  artifact: WidgetArtifact | null;
  draft_artifact: WidgetArtifact | null;
  quality_status: string;
  usage: TokenUsage;
  elapsed_seconds: number;
  error_code: string | null;
  cancel_requested: boolean;
}

export interface AuthSessionSnapshot {
  enabled: boolean;
  authenticated: boolean;
  user_id: number | null;
  email: string | null;
  csrf_token: string | null;
}

export type BillingPaymentStatus =
  | 'creating'
  | 'pending'
  | 'waiting_for_capture'
  | 'succeeded'
  | 'cancelled'
  | 'failed';

export interface BillingPayment {
  id: string;
  plan_code: string;
  status: BillingPaymentStatus;
  amount_minor: number;
  currency: string;
  created_at: string;
}

export interface BillingCheckout {
  payment: BillingPayment;
  checkout_url: string;
  created: boolean;
}

export interface PendingBillingCheckout {
  payment: BillingPayment | null;
  checkout_url: string | null;
}

export interface BillingSubscription {
  id: string;
  plan_code: string;
  status: 'pending' | 'active' | 'past_due' | 'cancelled' | 'expired';
  current_period_start: string;
  current_period_end: string;
}

export interface SaasProject {
  id: string;
  tenant_id: number;
  owner_user_id: number;
  source_url: string;
  brief: string | null;
  status: string;
  active_revision: number | null;
  active_run: SaasRunSnapshot | null;
  created_at: string;
  updated_at: string;
}

export interface SaasEvent {
  sequence: number;
  type: string;
  message: string | null;
  payload: {
    status?: string;
    stage?: BuilderStage | null;
    revision?: number | null;
    usage?: Partial<TokenUsage>;
    issues?: ValidationIssue[];
    changes?: string[];
    error_code?: string | null;
  };
  created_at: string | null;
}

export interface SaasPreviewArtifact extends Partial<WidgetArtifact> {
  id?: string;
  revision: number;
  body_html: string;
  css: string;
  javascript: string;
  quality_status?: string;
  source: 'accepted_artifact' | 'restorable_draft' | 'artifact' | string;
}

export interface SaasRunSnapshot {
  id: string;
  project_id: string;
  mode: 'express' | string;
  /** Canonical status returned by the SaaS API. */
  status: BuilderRunStatus;
  /** Temporary compatibility mirror; consumers must prefer status. */
  state?: BuilderRunStatus;
  progress: number;
  current_stage: BuilderStage | null;
  last_completed_stage: BuilderStage | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  latest_sequence: number;
  events?: SaasEvent[];
  preview?: SaasPreviewArtifact | null;
}

export interface BuilderRunInput {
  source_url: string;
  brief: string;
  engine: BuilderEngine;
  creativity: number;
  locale: 'ru';
  max_repairs: number;
}

export interface StudioError {
  message: string;
  raw: string;
  code: string | null;
}

export type PreviewViewport = 'desktop' | 'mobile';
