/**
 * Species marked 'unlikely' in docs/yard_list.json (pelagic birds, shorebirds, …)
 * are hidden by default. This shows how many, and flips them back in. Their
 * detections are never deleted — only filtered from this view.
 */
export function UnlikelyToggle({
  total,
  hidden,
  show,
  setShow,
}: {
  total: number
  hidden: number
  show: boolean
  setShow: (v: boolean) => void
}) {
  if (total === 0) return null
  return (
    <div className="unlikely-toggle lbl">
      {show ? (
        <>
          showing {total} unlikely species
        </>
      ) : (
        <>
          {hidden} unlikely species hidden
        </>
      )}
      <button className="chip" onClick={() => setShow(!show)}>
        {show ? 'Hide them' : 'Show them'}
      </button>
    </div>
  )
}
