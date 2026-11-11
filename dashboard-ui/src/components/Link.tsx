import type { AnchorHTMLAttributes } from 'react'
import { navigate } from '../lib/route'

/** In-app link: client-side navigation for plain left clicks, normal link otherwise. */
export function Link({
  href,
  onClick,
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  return (
    <a
      href={href}
      onClick={(e) => {
        onClick?.(e)
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)
          return
        e.preventDefault()
        navigate(href)
      }}
      {...rest}
    />
  )
}
