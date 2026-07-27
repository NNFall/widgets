export type BuilderEngine = 'direct' | 'antigravity';
export type BuilderRunStatus = 'created' | 'running' | 'completed' | 'failed' | 'cancelled';
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
