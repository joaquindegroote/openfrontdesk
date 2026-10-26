import type { Metadata } from "next";
import { Inbox } from "@/components/inbox";

export const metadata: Metadata = { title: "Staff inbox · openfrontdesk" };

export default function InboxPage() {
  return <Inbox />;
}
