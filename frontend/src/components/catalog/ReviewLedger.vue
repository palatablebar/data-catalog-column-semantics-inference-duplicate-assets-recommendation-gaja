<template>
  <aside class="ledger ink-panel">
    <p class="smallcaps ledger__title">复核台账 REVIEW LEDGER · {{ reviews.length }} 轮</p>
    <p class="ledger__hint mono">结论与当时依据绑定；依据变化后旧轮次归档并生成新待办。</p>
    <ol class="ledger__list">
      <li
        v-for="review in reversedReviews"
        :key="review.id"
        class="ledger__item"
        :class="stateClass(review.state)"
      >
        <header class="ledger__head">
          <span class="ledger__gen mono">第 {{ review.generation }} 代</span>
          <span class="ledger__state">{{ stateLabel(review.state) }}</span>
          <span v-if="isActive(review)" class="ledger__current mono">CURRENT</span>
        </header>

        <p class="ledger__basis mono" :title="review.basis_hash ?? ''">
          依据 {{ shortHash(review.basis_hash) }}
        </p>

        <template v-if="review.state === 'pending'">
          <p class="ledger__note">等待人工复核。</p>
        </template>
        <template v-else>
          <p v-if="review.review_note" class="ledger__note">原因：{{ review.review_note }}</p>
          <p class="ledger__meta mono">
            {{ review.reviewed_by ?? '—' }} · {{ review.reviewed_at ? formatDate(review.reviewed_at) : '—' }}
          </p>
        </template>

        <div v-if="review.basis_change && review.basis_change.changed" class="ledger__change">
          <p class="smallcaps ledger__change-title">相对上一轮依据的变化</p>
          <ul class="ledger__change-list">
            <li v-for="line in changeLines(review.basis_change)" :key="line" class="mono">{{ line }}</li>
          </ul>
        </div>

        <details v-if="review.basis" class="ledger__details">
          <summary class="smallcaps">查看本轮依据快照</summary>
          <div class="ledger__snapshot">
            <p class="mono">
              A {{ review.basis.left.asset_key }}：{{ review.basis.left.column_count }} 列 /
              {{ review.basis.left.row_count }} 行
            </p>
            <p class="mono ledger__sigs">{{ review.basis.left.column_signatures.join(' · ') }}</p>
            <p class="mono">
              B {{ review.basis.right.asset_key }}：{{ review.basis.right.column_count }} 列 /
              {{ review.basis.right.row_count }} 行
            </p>
            <p class="mono ledger__sigs">{{ review.basis.right.column_signatures.join(' · ') }}</p>
            <p class="mono">
              score {{ review.basis.metrics.score }} · jaccard {{ review.basis.metrics.jaccard }} ·
              结构 {{ review.basis.metrics.structure_score }}
            </p>
          </div>
        </details>
      </li>
    </ol>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { BasisChange, PairReview } from '../../types/models'
import { PAIR_REVIEW_STATE_LABELS } from '../../constants/enums'

const props = defineProps<{
  reviews: PairReview[]
  activeReviewId?: string | null
}>()

const reversedReviews = computed(() =>
  [...props.reviews].sort((a, b) => b.generation - a.generation),
)

function stateLabel(state: string) {
  return PAIR_REVIEW_STATE_LABELS[state] ?? state
}

function stateClass(state: string) {
  return `is-${state}`
}

function isActive(review: PairReview) {
  return props.activeReviewId === review.id
}

function shortHash(hash: string | null) {
  return hash ? `${hash.slice(0, 12)}…` : '历史数据（重算后补全）'
}

function formatDate(value: string) {
  return value.replace('T', ' ').slice(0, 16)
}

function changeLines(change: BasisChange): string[] {
  const lines: string[] = []
  const push = (label: string, values: string[]) => {
    values.forEach((value) => lines.push(`${label} ${value}`))
  }
  push('A 新增列', change.left_columns_added)
  push('A 移除列', change.left_columns_removed)
  push('B 新增列', change.right_columns_added)
  push('B 移除列', change.right_columns_removed)
  Object.entries(change.row_count_delta).forEach(([side, delta]) => {
    lines.push(`${side === 'left' ? 'A' : 'B'} 行数变化 ${delta > 0 ? '+' : ''}${delta}`)
  })
  Object.entries(change.metrics_delta).forEach(([key, delta]) => {
    lines.push(`${key} 变化 ${delta > 0 ? '+' : ''}${delta}`)
  })
  return lines.length ? lines : ['依据哈希变化（口径版本更新）']
}
</script>

<style scoped>
.ledger {
  padding: 12px;
  grid-column: 1 / -1;
}
.ledger__title {
  color: var(--ink-faint);
  margin-bottom: 4px;
}
.ledger__hint {
  font-size: 9.5px;
  color: var(--ink-faint);
  margin-bottom: 10px;
}
.ledger__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
}
.ledger__item {
  border: 1px solid var(--line-soft);
  border-left-width: 3px;
  padding: 8px 10px;
  background: color-mix(in oklch, var(--sand-raise) 60%, transparent);
}
.ledger__item.is-pending {
  border-left-color: var(--brick);
}
.ledger__item.is-dismissed {
  border-left-color: var(--ink-faint);
}
.ledger__item.is-merged {
  border-left-color: var(--moss);
}
.ledger__item.is-superseded {
  border-left-color: var(--line);
  opacity: 0.85;
}
.ledger__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ledger__gen {
  font-size: 10.5px;
  color: var(--ink);
  font-weight: 600;
}
.ledger__state {
  font-size: 9.5px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--ink-soft);
  border: 1px solid var(--line);
  padding: 1px 6px;
}
.ledger__current {
  margin-left: auto;
  font-size: 9px;
  color: var(--brick);
}
.ledger__basis {
  margin: 6px 0 4px;
  font-size: 9.5px;
  color: var(--ink-faint);
  word-break: break-all;
}
.ledger__note {
  margin: 4px 0;
  font-size: 10.5px;
  color: var(--ink-soft);
}
.ledger__meta {
  font-size: 9px;
  color: var(--ink-faint);
}
.ledger__change {
  margin-top: 6px;
  padding: 6px 8px;
  background: color-mix(in oklch, var(--brick-wash) 55%, transparent);
  border: 1px dashed var(--brick-soft);
}
.ledger__change-title {
  color: var(--brick);
  font-size: 9px;
  margin-bottom: 4px;
}
.ledger__change-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 2px;
  font-size: 9.5px;
  color: var(--ink-soft);
}
.ledger__details {
  margin-top: 6px;
}
.ledger__details summary {
  cursor: pointer;
  font-size: 9px;
  color: var(--ink-faint);
}
.ledger__snapshot {
  margin-top: 6px;
  display: grid;
  gap: 3px;
}
.ledger__sigs {
  font-size: 9px;
  color: var(--ink-faint);
  word-break: break-all;
}
</style>
