import type { Metadata } from "next";
import { IBM_Plex_Sans, JetBrains_Mono } from "next/font/google";
import { AuthProvider } from "@/lib/auth-context";
import { ThemeProvider } from "@/lib/theme-context";
import { ToastProvider } from "@/lib/toast-context";
import "./globals.css";

const sans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Gunivox · Admissions Dialer",
  description: "Outbound admissions calling console for Ganpat University.",
};

// Runs before React hydrates. Reads the stored theme (or OS preference) and
// applies the `dark` class to <html> so first paint matches user preference.
const themeBootScript = `
(function() {
  try {
    var t = localStorage.getItem('gunivox.theme');
    if (!t) t = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    if (t === 'dark') document.documentElement.classList.add('dark');
  } catch (e) {}
})();
`;

// Synchronous auth guard. Runs in <head> before React mounts and before
// any body content paints. If the current path is a protected route and
// there's no JWT in localStorage, the browser bounces straight to /login
// without ever rendering the protected shell — "real application" feel
// for someone pasting a URL into a fresh tab.
const authGuardScript = `
(function() {
  try {
    var publicPaths = ['/login', '/signup', '/forgot-password', '/reset-password'];
    var path = window.location.pathname;
    var isPublic = path === '/' || publicPaths.some(function(p) {
      return path === p || path.indexOf(p + '/') === 0;
    });
    if (!isPublic && !localStorage.getItem('gunivox.jwt')) {
      var dest = '/login?next=' + encodeURIComponent(path + window.location.search);
      window.location.replace(dest);
    }
  } catch (e) {}
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${sans.variable} ${mono.variable} dark`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
        <script dangerouslySetInnerHTML={{ __html: authGuardScript }} />
      </head>
      <body suppressHydrationWarning>
        <ThemeProvider>
          <AuthProvider>
            <ToastProvider>{children}</ToastProvider>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
