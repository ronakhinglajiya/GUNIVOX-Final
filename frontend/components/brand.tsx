"use client";

import { useState } from "react";
import { Brain } from "lucide-react";
import clsx from "clsx";

/**
 * <Logo> renders /jatas-logo.png if present. If the file is missing or fails
 * to load, falls back to a stylised lucide Brain icon in the brand red so the
 * UI never looks broken.
 *
 * Drop the actual file at `frontend/public/jatas-logo.png` and it appears
 * everywhere this component is used.
 */
interface LogoProps {
  size?: number;
  className?: string;
}

export function Logo({ size = 40, className }: LogoProps) {
  const [err, setErr] = useState(false);
  return (
    <div
      className={clsx("inline-flex items-center justify-center", className)}
      style={{ width: size, height: size }}
    >
      {err ? (
        <div
          className="flex items-center justify-center bg-brand/10 rounded"
          style={{ width: size, height: size }}
        >
          <Brain className="text-brand" style={{ width: size * 0.7, height: size * 0.7 }} />
        </div>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src="/jatas-logo.png"
          alt="JATAS AI"
          width={size}
          height={size}
          onError={() => setErr(true)}
          className="object-contain"
        />
      )}
    </div>
  );
}

/**
 * The small "Powered by" attribution. Used in login, landing, sidebar footer.
 * Subtle by default; pass `variant="bold"` for the login page hero treatment.
 */
export function PoweredBy({
  variant = "subtle",
  showLogo = false,
}: {
  variant?: "subtle" | "bold";
  showLogo?: boolean;
}) {
  if (variant === "bold") {
    return (
      <div className="flex flex-col items-center gap-3">
        {showLogo && <Logo size={48} />}
        <div className="text-center">
          <p className="text-[11px] uppercase tracking-widest text-ink-faint">Powered by</p>
          <p className="mt-1 text-sm font-semibold text-ink">
            <span className="text-brand">JATAS.AI</span>
            <span className="text-ink-faint mx-2">·</span>
            <span>TOMATOVATION</span>
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-center justify-center gap-1.5 text-[10px] uppercase tracking-widest text-ink-faint">
      {showLogo && <Logo size={14} />}
      <span>powered by</span>
      <span className="text-brand font-semibold">JATAS.AI</span>
      <span>·</span>
      <span className="font-semibold">TOMATOVATION</span>
    </div>
  );
}
