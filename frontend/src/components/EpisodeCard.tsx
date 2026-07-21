import { EpisodeRow } from './shared/EpisodeRow'
import type { MockEpisode } from '../routes/episodeData'

export function EpisodeCard({ ep }: { readonly ep: MockEpisode }) {
  return <EpisodeRow ep={ep} variant="card" />
}
