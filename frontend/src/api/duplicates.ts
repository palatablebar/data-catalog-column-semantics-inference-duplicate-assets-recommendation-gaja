import { http } from './http'
import type { SimilarityPair } from '../types/models'

export interface PairListResponse {
  items: SimilarityPair[]
  total: number
  recommended_total: number
  dismissed_total: number
  pending_review_total: number
  true_duplicate_total: number
}

export async function fetchPairs(limit = 5) {
  const { data } = await http.get('/duplicates', { params: { limit } })
  return data as PairListResponse
}

export async function recomputePairs(topN = 6) {
  const { data } = await http.post('/duplicates/recompute', { top_n: topN, min_score: 0.45 })
  return data as {
    created: number
    updated: number
    reopened_pairs: number
    superseded_pairs: number
    unchanged_decisions: number
    recommendations: SimilarityPair[]
  }
}

export async function fetchPair(pairId: string) {
  const { data } = await http.get(`/duplicates/${pairId}`)
  return data as SimilarityPair
}

export async function generateMergeSuggestion(pairId: string) {
  const { data } = await http.post(`/duplicates/${pairId}/merge-suggestion`)
  return data
}

export async function dismissPair(pairId: string, note = '人工确认非重复') {
  const { data } = await http.post(`/duplicates/${pairId}/dismiss`, { note })
  return data
}

export async function reopenPair(pairId: string, note = '人工推翻旧结论，重新复核') {
  const { data } = await http.post(`/duplicates/${pairId}/reopen`, { note })
  return data
}
