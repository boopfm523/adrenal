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

export function NotFoundPage(): React.JSX.Element {
  return (
    <Page title="Page not found" description="The requested page does not exist.">
      <Link to="/today">Return to Today</Link>
    </Page>
  );
}
