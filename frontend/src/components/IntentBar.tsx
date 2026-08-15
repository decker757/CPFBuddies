import { useState } from 'react'

/**
 * Where the delegation happens: the buyer's own words and the cap they approve.
 *
 * The cap is a separate field rather than parsed out of the sentence. The
 * mandate binds to this number and the contract enforces it, so guessing it
 * from prose would put an LLM between a person and their own spending limit.
 */
export function IntentBar({
  busy,
  onSubmit,
}: {
  busy: boolean
  onSubmit: (intent: string, cap: string) => void
}) {
  const [intent, setIntent] = useState('toothbrush under $5')
  const [cap, setCap] = useState('5.00')

  const capValid = /^\d+(\.\d{1,6})?$/.test(cap) && Number(cap) > 0
  const ready = intent.trim().length > 0 && capValid && !busy

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        if (ready) onSubmit(intent.trim(), cap)
      }}
      className="flex flex-col gap-3 sm:flex-row sm:items-end"
    >
      <label className="flex-1">
        <span className="block text-xs font-semibold tracking-wide text-charcoal uppercase">
          What do you want?
        </span>
        <input
          value={intent}
          onChange={(event) => setIntent(event.target.value)}
          disabled={busy}
          maxLength={200}
          placeholder="toothbrush under $5"
          className="mt-1 h-11 w-full rounded-full border border-hairline bg-surface-card px-5 text-ink outline-none focus:border-hairline-strong disabled:opacity-60"
        />
      </label>

      <label className="sm:w-40">
        <span className="block text-xs font-semibold tracking-wide text-charcoal uppercase">
          Spend up to
        </span>
        <div className="relative mt-1">
          <input
            value={cap}
            onChange={(event) => setCap(event.target.value)}
            disabled={busy}
            inputMode="decimal"
            aria-invalid={!capValid}
            className="h-11 w-full rounded-full border border-hairline bg-surface-card pr-16 pl-5 font-mono text-ink outline-none focus:border-hairline-strong disabled:opacity-60"
          />
          <span className="absolute top-1/2 right-4 -translate-y-1/2 font-mono text-xs text-ash">
            XSGD
          </span>
        </div>
      </label>

      <button
        type="submit"
        disabled={!ready}
        className="h-11 rounded-full bg-ink px-7 font-semibold text-on-dark transition disabled:opacity-40"
      >
        {busy ? 'Running…' : 'Delegate'}
      </button>
    </form>
  )
}
