import { Link } from "react-router-dom";

import { Page } from "../components/Page";

interface PlaceholderPageProps {
  title: string;
  description: string;
}

export function PlaceholderPage({ title, description }: PlaceholderPageProps): React.JSX.Element {
  return (
    <Page title={title} description={description}>
      <section className="empty-state" aria-labelledby="foundation-status">
        <h2 id="foundation-status">This view is ready for its first records</h2>
        <p>The secure application foundation is active. This page will be completed in its dedicated implementation issue.</p>
      </section>
    </Page>
  );
}

export function TodayPage(): React.JSX.Element {
  return (
    <Page title="Today" description="Recorded facts and your physician-approved plan remain separate.">
      <div className="quick-actions" aria-label="Quick actions">
        <Link className="button-link" to="/timeline">Open timeline</Link>
        <a className="button-link button-link--urgent" href="/emergency">Open emergency plan</a>
      </div>
      <section className="empty-state" aria-labelledby="today-empty">
        <h2 id="today-empty">No daily summary loaded</h2>
        <p>No missing record is treated as zero. Today’s data will appear here only when the dedicated Today page is implemented.</p>
      </section>
    </Page>
  );
}

export function NotFoundPage(): React.JSX.Element {
  return (
    <Page title="Page not found" description="The requested page does not exist.">
      <Link to="/today">Return to Today</Link>
    </Page>
  );
}
