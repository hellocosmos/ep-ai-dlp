use anyhow::{anyhow, ensure};

pub type PID = u32;

#[derive(Debug, Clone)]
pub struct ProcessInfo {
    pub pid: PID,
    pub process_name: Option<String>,
}

#[derive(PartialEq, Eq, Debug, Clone)]
pub struct InterceptConf {
    default: bool,
    actions: Vec<Action>,
}

#[derive(PartialEq, Eq, Debug, Clone)]
enum Action {
    Include(Pattern),
    Exclude(Pattern),
}

#[derive(PartialEq, Eq, Debug, Clone)]
enum Pattern {
    Pid(PID),
    Process(String),
    ExactProcess(String),
}

impl Pattern {
    #[inline(always)]
    fn matches(&self, process_info: &ProcessInfo) -> bool {
        match self {
            Pattern::Pid(pid) => process_info.pid == *pid,
            Pattern::ExactProcess(path) => process_info.process_name.as_ref().is_some_and(|name| normalize_windows_path(name) == *path),
            Pattern::Process(name) => process_info
                .process_name
                .as_ref()
                .map(|n| n.contains(name))
                .unwrap_or(false),
        }
    }
}

impl TryFrom<&str> for InterceptConf {
    type Error = anyhow::Error;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        let val = value.trim();
        if val.is_empty() {
            return Ok(InterceptConf::new(vec![]));
        }
        let actions: Vec<&str> = val.split(',').collect();
        InterceptConf::try_from(actions).map_err(|_| anyhow!("invalid intercept spec: {value}"))
    }
}

impl<T: AsRef<str>> TryFrom<Vec<T>> for InterceptConf {
    type Error = anyhow::Error;

    fn try_from(value: Vec<T>) -> Result<Self, Self::Error> {
        let actions = value
            .into_iter()
            .map(|a| Action::try_from(a.as_ref()))
            .collect::<Result<Vec<_>, _>>()?;
        Ok(InterceptConf::new(actions))
    }
}

impl TryFrom<&str> for Action {
    type Error = anyhow::Error;
    fn try_from(value: &str) -> Result<Self, Self::Error> {
        let value = value.trim();
        if let Some(value) = value.strip_prefix('!') {
            Ok(Action::Exclude(Pattern::try_from(value)?))
        } else {
            Ok(Action::Include(Pattern::try_from(value)?))
        }
    }
}

impl TryFrom<&str> for Pattern {
    type Error = anyhow::Error;
    fn try_from(value: &str) -> Result<Self, Self::Error> {
        let value = value.trim();
        ensure!(!value.is_empty(), "pattern must not be empty");
        if let Some(path) = value.strip_prefix("=") {
            let path = normalize_windows_path(path);
            let bytes = path.as_bytes();
            ensure!(bytes.len() > 7 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':' && bytes[2] == b'\\', "application selector requires an absolute Windows path");
            ensure!(path.ends_with(".exe") && !path.contains([',', '\n', '\r', '*', '?', '!', '=']) && !path.split('\\').any(|part| part == ".." || part == "."), "invalid application selector");
            return Ok(Pattern::ExactProcess(path));
        }
        Ok(match value.parse::<PID>() {
            Ok(pid) => Pattern::Pid(pid),
            Err(_) => Pattern::Process(value.to_string()),
        })
    }
}

impl std::fmt::Display for Action {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Action::Include(pat) => write!(f, "{pat}"),
            Action::Exclude(pat) => write!(f, "!{pat}"),
        }
    }
}

impl std::fmt::Display for Pattern {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Pattern::Pid(pid) => write!(f, "{pid}"),
            Pattern::Process(name) => write!(f, "{name}"),
            Pattern::ExactProcess(path) => write!(f, "={path}"),
        }
    }
}

pub fn normalize_windows_path(path: &str) -> String { path.replace('/', "\\").to_lowercase() }

impl InterceptConf {
    /// Exact application mode never accepts substring or exclusion patterns.
    pub fn is_application_scope(&self) -> bool {
        !self.default && !self.actions.is_empty() && self.actions.iter().all(|a| matches!(a, Action::Include(Pattern::ExactProcess(_))))
    }

    fn new(actions: Vec<Action>) -> Self {
        let default = matches!(actions.first(), Some(Action::Exclude(_)));
        Self { default, actions }
    }

    pub fn disabled() -> Self {
        Self::new(vec![])
    }

    pub fn actions(&self) -> Vec<String> {
        self.actions.iter().map(|a| a.to_string()).collect()
    }

    pub fn default(&self) -> bool {
        self.default
    }

    pub fn should_intercept(&self, process_info: &ProcessInfo) -> bool {
        let mut intercept = self.default;
        for action in &self.actions {
            match action {
                Action::Include(pattern) => {
                    intercept = intercept || pattern.matches(process_info);
                }
                Action::Exclude(pattern) => {
                    intercept = intercept && !pattern.matches(process_info);
                }
            }
        }
        intercept
    }

    pub fn description(&self) -> String {
        if self.actions.is_empty() {
            return "Intercept nothing.".to_string();
        }
        let parts: Vec<String> = self
            .actions
            .iter()
            .map(|a| match a {
                Action::Include(Pattern::ExactProcess(path)) => format!("Include application {path}."),
                Action::Exclude(Pattern::ExactProcess(path)) => format!("Exclude application {path}."),
                Action::Include(Pattern::Pid(pid)) => format!("Include PID {pid}."),
                Action::Include(Pattern::Process(name)) => {
                    format!("Include processes matching \"{name}\".")
                }
                Action::Exclude(Pattern::Pid(pid)) => format!("Exclude PID {pid}."),
                Action::Exclude(Pattern::Process(name)) => {
                    format!("Exclude processes matching \"{name}\".")
                }
            })
            .collect();
        parts.join(" ")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_applications_do_not_match_lookalike_paths() {
        let conf = InterceptConf::try_from(r"=C:\Apps\Browser.exe").unwrap();
        assert!(conf.is_application_scope());
        for path in [r"c:\apps\browser.exe", "C:/APPS/BROWSER.EXE"] {
            assert!(conf.should_intercept(&ProcessInfo { pid: 88, process_name: Some(path.into()) }));
        }
        for path in [r"C:\Apps\Browser.exe.bad", r"C:\Other\Browser.exe", r"C:\Apps\OtherBrowser.exe"] {
            assert!(!conf.should_intercept(&ProcessInfo { pid: 88, process_name: Some(path.into()) }));
        }
        assert!(!conf.should_intercept(&ProcessInfo { pid: 88, process_name: None }));
        assert!(!InterceptConf::try_from("browser.exe").unwrap().is_application_scope());
        for value in ["=browser.exe", r"=C:\Apps\..\browser.exe", r"=C:\Apps\*.exe"] {
            assert!(InterceptConf::try_from(value).is_err());
        }
    }

    #[test]
    fn test_intercept_conf() {
        let a = ProcessInfo {
            pid: 1,
            process_name: Some("a".into()),
        };
        let b = ProcessInfo {
            pid: 2242,
            process_name: Some("mitmproxy".into()),
        };

        let conf = InterceptConf::try_from("1,2,3").unwrap();
        assert!(conf.should_intercept(&a));
        assert!(!conf.should_intercept(&b));

        let conf = InterceptConf::try_from("").unwrap();
        assert!(!conf.should_intercept(&a));
        assert!(!conf.should_intercept(&b));
        assert_eq!(conf, InterceptConf::disabled());

        let conf = InterceptConf::try_from("!1234").unwrap();
        assert!(conf.should_intercept(&a));
        assert!(conf.should_intercept(&b));

        let conf = InterceptConf::try_from("mitm").unwrap();
        assert!(!conf.should_intercept(&a));
        assert!(conf.should_intercept(&b));

        assert!(InterceptConf::try_from(",,").is_err());
    }
}
