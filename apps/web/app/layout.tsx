import type { Metadata } from "next";
import "./style.css";

export const metadata: Metadata = {
  title: "DisputeShield — Prevention console",
  description: "Merchant scoped payment dispute monitoring and case management.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
