import { useState } from "react";
import { resolveTokenIconUrl } from "./tokenIconUrls";

type Props = {
  symbol: string;
  iconUrl?: string | null;
  size?: number;
};

export function TokenIcon({ symbol, iconUrl, size = 36 }: Props): JSX.Element {
  const [broken, setBroken] = useState(false);
  const src = resolveTokenIconUrl(symbol, iconUrl);
  const label = symbol.trim() || "?";
  const initials = label.slice(0, 2).toUpperCase();

  if (!src || broken) {
    return (
      <span
        className="token-icon token-icon-fallback"
        style={{ width: size, height: size, fontSize: size * 0.36 }}
        aria-hidden
      >
        {initials}
      </span>
    );
  }

  return (
    <img
      className="token-icon"
      src={src}
      alt=""
      width={size}
      height={size}
      loading="lazy"
      decoding="async"
      onError={() => setBroken(true)}
    />
  );
}
