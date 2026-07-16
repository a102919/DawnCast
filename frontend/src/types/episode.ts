export type Cue = {
  readonly index: number
  readonly speaker: string
  readonly text: string
  readonly zh: string
  readonly start: number
  readonly end: number
}

export type Episode = {
  readonly id: string
  readonly title: string
  readonly videoUrl: string
  readonly cues: readonly Cue[]
}
