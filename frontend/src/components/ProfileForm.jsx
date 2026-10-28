const STORAGE_KEY = "recommend-profile";
const EMPTY_PROFILE = {
  skills: "",
  level: "",
  interests: "",
  mode_preference: "",
  location: "",
};

export function loadProfile() {
  try {
    return { ...EMPTY_PROFILE, ...JSON.parse(localStorage.getItem(STORAGE_KEY)) };
  } catch {
    return EMPTY_PROFILE;
  }
}

export function saveProfile(profile) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(profile));
  } catch {
    // storage unavailable; the profile just won't persist
  }
}

export function isProfileEmpty(profile) {
  return Object.values(profile).every((v) => !v.trim());
}

function toList(text) {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function profileToRequest(profile) {
  return {
    skills: toList(profile.skills),
    level: profile.level,
    interests: toList(profile.interests),
    mode_preference: profile.mode_preference,
    location: profile.location.trim(),
  };
}

export default function ProfileForm({ profile, onChange, disabled }) {
  function update(field, value) {
    onChange({ ...profile, [field]: value });
  }

  return (
    <section className="profile-section">
      <div className="profile-form">
        <label>
          Skills
          <input
            value={profile.skills}
            onChange={(e) => update("skills", e.target.value)}
            placeholder="python, sql"
            disabled={disabled}
          />
        </label>
        <label>
          Interests
          <input
            value={profile.interests}
            onChange={(e) => update("interests", e.target.value)}
            placeholder="machine learning, web dev"
            disabled={disabled}
          />
        </label>
        <label>
          Level
          <select
            value={profile.level}
            onChange={(e) => update("level", e.target.value)}
            disabled={disabled}
          >
            <option value="">Any</option>
            <option value="beginner">Beginner</option>
            <option value="intermediate">Intermediate</option>
            <option value="advanced">Advanced</option>
          </select>
        </label>
        <label>
          Mode
          <select
            value={profile.mode_preference}
            onChange={(e) => update("mode_preference", e.target.value)}
            disabled={disabled}
          >
            <option value="">Any</option>
            <option value="online">Online</option>
            <option value="offline">Offline</option>
            <option value="hybrid">Hybrid</option>
          </select>
        </label>
        <label>
          Location
          <input
            value={profile.location}
            onChange={(e) => update("location", e.target.value)}
            placeholder="Hyderabad"
            disabled={disabled}
          />
        </label>
      </div>

      <p className="hint">Leave any filter empty to use the default (no filtering).</p>
    </section>
  );
}
