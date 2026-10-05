import { Link } from 'react-router-dom';
import { SETTINGS_ITEMS } from '../nav';
import styles from './SettingsPage.module.css';

export default function SettingsPage() {
  return (
    <ul className={styles.list}>
      {SETTINGS_ITEMS.map((item) => (
        <li key={item.to}>
          <Link to={item.to} className={styles.item}>
            <span>
              <span className={styles.label}>{item.label}</span>
              <span className={styles.desc}>{item.desc}</span>
            </span>
            <span className={styles.arrow}>›</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
