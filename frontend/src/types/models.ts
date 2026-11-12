export type Role = 'admin' | 'analyst' | 'viewer'
export type ColumnSemanticType =
  | 'phone'
  | 'id_card'
  | 'email'
  | 'amount'
  | 'datetime'
  | 'enum'
  | 'ambiguous'
  | 'unknown'

export interface User {
  id: string
  email: string
  display_name: string
  role: Role
}

export interface TagItem {
  id: string
  tag_name: string
  category: string
  source: 'manual' | 'inferred'
  confidence: number
  tagged_by: string
  created_at: string | null
}

export interface ColumnItem {
  id: string
  column_name: string
  data_type: string
  inferred_type: ColumnSemanticType | null
  confidence: number
  inference_source: 'auto' | 'manual' | null
  evidence?: string[]
  candidates?: { type: string; score: number }[]
  sample_values?: (string | null)[]
  profiled?: boolean
  stats?: {
    sample_count: number
    null_rate: number
    unique_count: number
    profile_source: string
  }
  asset?: AssetBrief
  tags?: TagItem[]
  ordinal?: number
  nullable?: boolean
  comment?: string
}

export interface AssetBrief {
  id: string
  asset_key: string
  table_name: string
  schema_name?: string
  layer: string
  domain: string
  row_count: number
  column_count: number
}

export interface AssetListItem extends AssetBrief {
  description?: string
  owner_name?: string
  source_kind?: 'sample_table' | 'catalog_metadata'
  is_active?: boolean
  updated_at?: string | null
  sensitive_tag_count?: number
  typed_column_count?: number
  columns_preview?: ColumnItem[]
}

export interface AssetDetail extends AssetListItem {
  columns: ColumnItem[]
}

export interface Facets {
  layers: { value: string; label: string; count: number }[]
  domains: { value: string; count: number }[]
  stats: {
    assets: number
    sample_tables: number
    metadata_only: number
    columns: number
    profiled_columns: number
    sensitive_columns: number
  }
}

export interface PairAsset extends AssetBrief {
  owner_name?: string
  source_kind?: string
}

export interface MergeSuggestion {
  drop_asset_key: string
  keep_asset_key: string
  union_columns: string[]
  extra_in_drop: string[]
  missing_in_drop: string[]
  score: number
  jaccard: number
  structure_score: number
  verdict: string
  actions: string[]
  merged_columns?: { left: string; right: string; ratio: number }[]
  generated_by?: string
  dismiss_note?: string
  dismissed_by?: string
}

export type PairReviewState = 'pending' | 'dismissed' | 'merged' | 'superseded'

export interface PairBasisSide {
  asset_key: string
  row_count: number
  column_count: number
  column_signatures: string[]
}

export interface PairBasis {
  engine_version: number
  left: PairBasisSide
  right: PairBasisSide
  metrics: {
    jaccard: number
    structure_score: number
    score: number
  }
}

export interface BasisChange {
  changed: boolean
  reason: string
  left_columns_added: string[]
  left_columns_removed: string[]
  right_columns_added: string[]
  right_columns_removed: string[]
  row_count_delta: Record<string, number>
  metrics_delta: Record<string, number>
}

export interface PairReview {
  id: string
  generation: number
  state: PairReviewState
  basis_hash: string | null
  basis: PairBasis | null
  basis_change: BasisChange | null
  review_note: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface SimilarityPair {
  id: string
  score: number
  jaccard: number
  structure_score: number
  method: string
  status: string
  verdict: string
  computed_at: string | null
  basis_hash: string | null
  basis?: PairBasis | null
  active_review?: PairReview | null
  reviews?: PairReview[]
  left: PairAsset
  right: PairAsset
  suggestion: MergeSuggestion | Record<string, never>
  left_columns?: ColumnItem[]
  right_columns?: ColumnItem[]
  diff?: DiffRow[]
}

export interface DiffRow {
  state: 'shared' | 'left_only' | 'right_only'
  left_column: ColumnItem | null
  right_column: ColumnItem | null
  similarity: number
}

export interface IngestJob {
  id: string
  job_name: string
  connection_name: string
  scope: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  stats: Record<string, number | string>
  error_code: string | null
  error_message: string | null
  triggered_by: string
  started_at: string | null
  finished_at: string | null
  created_at: string | null
}

export interface TagRow extends TagItem {
  column: {
    id: string
    column_name: string
    data_type: string
    inferred_type: ColumnSemanticType | null
    confidence: number
  }
  asset: PairAsset
}

export interface Overview {
  assets: number
  columns: number
  inferred_columns: number
  semantic_tags: number
  sensitive_columns: number
  similarity_pairs: number
  pending_pairs: number
  ingest_jobs: number
  completed_jobs: number
}
