//! Complete, bounded, fail-closed request inspection without content retention.
use crate::detectors::detect;
use regex::Regex;
use serde::de::{self, DeserializeSeed, MapAccess, SeqAccess, Visitor};
use std::{
    collections::{BTreeSet, HashSet},
    fmt,
    sync::LazyLock,
};

pub const MAX_BODY_BYTES: usize = 1024 * 1024;
pub const MAX_PATH_BYTES: usize = 16384;
pub const MAX_FORM_FIELDS: usize = 1024;
pub const MAX_DEPTH: usize = 32;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Decision {
    pub reason: &'static str,
    pub rules: Vec<&'static str>,
}
impl Decision {
    pub fn uninspectable() -> Self {
        Self {
            reason: "uninspectable_request",
            rules: vec![],
        }
    }
}

fn decode(value: &str, plus: bool, ascii: bool) -> Result<String, ()> {
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' => {
                let hex = |b: u8| match b {
                    b'0'..=b'9' => Some(b - b'0'),
                    b'a'..=b'f' => Some(b - b'a' + 10),
                    b'A'..=b'F' => Some(b - b'A' + 10),
                    _ => None,
                };
                if i + 2 >= bytes.len() {
                    return Err(());
                }
                out.push(hex(bytes[i + 1]).ok_or(())? * 16 + hex(bytes[i + 2]).ok_or(())?);
                i += 3;
            }
            b'+' if plus => {
                out.push(b' ');
                i += 1;
            }
            b => {
                out.push(b);
                i += 1;
            }
        }
    }
    if ascii && !out.is_ascii() {
        return Err(());
    }
    String::from_utf8(out).map_err(|_| ())
}

static MEDIA: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^([!#$%&'*+.^_`|~0-9A-Za-z-]+)/([!#$%&'*+.^_`|~0-9A-Za-z-]+)").unwrap()
});
static PARAM: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"^[ \t]*;[ \t]*([!#$%&'*+.^_`|~0-9A-Za-z-]+)[ \t]*=[ \t]*([!#$%&'*+.^_`|~0-9A-Za-z-]+|"(?:[\t\x20\x21\x23-\x5b\x5d-\x7e]|\\[\t\x20-\x7e])*")"#).unwrap()
});
fn media(value: &str) -> Result<(String, bool), ()> {
    let value = value.trim_matches([' ', '\t']);
    let cap = MEDIA.captures(value).ok_or(())?;
    let kind = format!("{}/{}", &cap[1], &cap[2]).to_ascii_lowercase();
    if !matches!(
        kind.as_str(),
        "application/json" | "text/plain" | "application/x-www-form-urlencoded"
    ) {
        return Err(());
    }
    let mut pos = cap.get(0).unwrap().end();
    let mut seen = HashSet::new();
    let mut charset = "utf-8".to_string();
    while pos < value.len() {
        let cap = PARAM.captures(&value[pos..]).ok_or(())?;
        let name = cap[1].to_ascii_lowercase();
        if !seen.insert(name.clone()) {
            return Err(());
        }
        let mut parameter = cap[2].to_string();
        if parameter.starts_with('"') {
            let mut chars = parameter[1..parameter.len() - 1].chars();
            let mut decoded = String::new();
            while let Some(c) = chars.next() {
                decoded.push(if c == '\\' {
                    chars.next().ok_or(())?
                } else {
                    c
                });
            }
            parameter = decoded;
        }
        if name == "charset" {
            charset = parameter.to_ascii_lowercase();
        }
        pos += cap.get(0).unwrap().end();
    }
    if !matches!(charset.as_str(), "utf-8" | "utf8" | "us-ascii") {
        return Err(());
    }
    Ok((kind, charset == "us-ascii"))
}

fn looks_json(value: &str) -> bool {
    let value = value.trim_start();
    value.starts_with(['{', '[', '"']) || matches!(value, "NaN" | "Infinity" | "-Infinity")
}
fn scan(text: &str, rules: &mut BTreeSet<&'static str>) {
    rules.extend(detect(text));
}
fn string(
    text: &str,
    depth: usize,
    embedded: bool,
    rules: &mut BTreeSet<&'static str>,
) -> Result<(), ()> {
    scan(text, rules);
    if embedded && looks_json(text) {
        json(text, depth + 1, embedded, rules)?;
    }
    Ok(())
}

struct Seed<'a> {
    depth: usize,
    embedded: bool,
    rules: &'a mut BTreeSet<&'static str>,
}
impl<'de> DeserializeSeed<'de> for Seed<'_> {
    type Value = ();
    fn deserialize<D: de::Deserializer<'de>>(self, deserializer: D) -> Result<(), D::Error> {
        if self.depth > MAX_DEPTH {
            return Err(de::Error::custom("json_depth"));
        }
        deserializer.deserialize_any(self)
    }
}
impl<'de> Visitor<'de> for Seed<'_> {
    type Value = ();
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("strict JSON")
    }
    fn visit_bool<E: de::Error>(self, _: bool) -> Result<(), E> {
        Ok(())
    }
    fn visit_unit<E: de::Error>(self) -> Result<(), E> {
        Ok(())
    }
    fn visit_i64<E: de::Error>(self, _: i64) -> Result<(), E> {
        Ok(())
    }
    fn visit_u64<E: de::Error>(self, _: u64) -> Result<(), E> {
        Ok(())
    }
    fn visit_f64<E: de::Error>(self, _: f64) -> Result<(), E> {
        Ok(())
    }
    fn visit_str<E: de::Error>(self, value: &str) -> Result<(), E> {
        string(value, self.depth, self.embedded, self.rules)
            .map_err(|_| E::custom("invalid embedded JSON"))
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<(), A::Error> {
        while seq
            .next_element_seed(Seed {
                depth: self.depth + 1,
                embedded: self.embedded,
                rules: self.rules,
            })?
            .is_some()
        {}
        Ok(())
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<(), A::Error> {
        let mut seen = HashSet::new();
        while let Some(key) = map.next_key::<String>()? {
            if !seen.insert(key.clone()) {
                return Err(de::Error::custom("duplicate JSON key"));
            }
            string(&key, self.depth + 1, self.embedded, self.rules)
                .map_err(|_| de::Error::custom("invalid embedded JSON key"))?;
            map.next_value_seed(Seed {
                depth: self.depth + 1,
                embedded: self.embedded,
                rules: self.rules,
            })?;
        }
        Ok(())
    }
}
fn json(
    text: &str,
    depth: usize,
    embedded: bool,
    rules: &mut BTreeSet<&'static str>,
) -> Result<(), ()> {
    let mut parser = serde_json::Deserializer::from_str(text);
    Seed {
        depth,
        embedded,
        rules,
    }
    .deserialize(&mut parser)
    .map_err(|_| ())?;
    parser.end().map_err(|_| ())?;
    // Include numeric lexemes; no lossy float/integer conversion enters detection.
    scan(text, rules);
    Ok(())
}

fn inspect_inner(
    method: &str,
    path: &str,
    content_type: &str,
    body: &[u8],
) -> Result<BTreeSet<&'static str>, ()> {
    if body.len() > MAX_BODY_BYTES
        || path.len() > MAX_PATH_BYTES
        || !matches!(
            method,
            "GET" | "HEAD" | "POST" | "PUT" | "PATCH" | "DELETE" | "OPTIONS"
        )
    {
        return Err(());
    }
    let mut rules = BTreeSet::new();
    scan(&decode(path, false, false)?, &mut rules);
    if body.is_empty() {
        return Ok(rules);
    }
    let text = std::str::from_utf8(body).map_err(|_| ())?;
    let (kind, ascii) = media(content_type)?;
    if ascii && !text.is_ascii() {
        return Err(());
    }
    match kind.as_str() {
        "application/json" => json(text, 0, false, &mut rules)?,
        "text/plain" => scan(text, &mut rules),
        "application/x-www-form-urlencoded" => {
            if text.split('&').count() > MAX_FORM_FIELDS {
                return Err(());
            }
            for field in text.split('&') {
                let (key, value) = field.split_once('=').ok_or(())?;
                for part in [key, value] {
                    string(&decode(part, true, ascii)?, 0, true, &mut rules)?;
                }
            }
        }
        _ => return Err(()),
    }
    Ok(rules)
}
pub fn inspect(method: &str, path: &str, content_type: &str, body: &[u8]) -> Decision {
    match inspect_inner(method, path, content_type, body) {
        Ok(rules) => Decision {
            reason: if rules.is_empty() {
                "clean"
            } else {
                "sensitive_data"
            },
            rules: rules.into_iter().collect(),
        },
        Err(()) => Decision::uninspectable(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn post(kind: &str, text: &str) -> Decision {
        inspect("POST", "/", kind, text.as_bytes())
    }
    #[test]
    fn review_numeric_bypass_probes_block_at_request_boundary() {
        for (body, rule) in [
            ("010 01012345678", "kr_phone"),
            ("123456 9001011234567", "kr_rrn"),
            ("010-１２３４-５６７８", "kr_phone"),
            ("９００１０１-1２３４５６７", "kr_rrn"),
            ("４１１１１１１１１１１１１１１１", "credit_card"),
        ] {
            let decision = post("text/plain", body);
            assert_eq!(decision.reason, "sensitive_data", "{body}");
            assert!(decision.rules.contains(&rule), "missing {rule}: {body}");
        }
        assert_eq!(
            post(
                "application/json",
                r#"{"phone":"010-\uff11\uff12\uff13\uff14-\uff15\uff16\uff17\uff18"}"#
            )
            .reason,
            "sensitive_data"
        );
        assert_eq!(post("application/x-www-form-urlencoded", "phone=010-%EF%BC%91%EF%BC%92%EF%BC%93%EF%BC%94-%EF%BC%95%EF%BC%96%EF%BC%97%EF%BC%98").reason, "sensitive_data");
    }
    #[test]
    fn decoded_keys_values_numbers_and_url() {
        for text in [
            r#"{"email":"person\u0040example.test"}"#,
            r#"{"person\u0040example.test":"value"}"#,
            r#"{"card":4111111111111111}"#,
        ] {
            assert_eq!(post("application/json", text).reason, "sensitive_data");
        }
        assert!(
            inspect("GET", "/?q=person%40example.test", "", b"")
                .rules
                .contains(&"email")
        );
        assert_eq!(
            inspect("GET", "/?q=%FF", "", b"").reason,
            "uninspectable_request"
        );
        assert_eq!(
            inspect("GET", "/?q=%xy", "", b"").reason,
            "uninspectable_request"
        );
        assert_eq!(
            post("application/json", r#"{"message":"hello"}"#).reason,
            "clean"
        );
    }
    #[test]
    fn strict_json_and_depth() {
        for text in [
            r#"{"x":"safe","x":"other"}"#,
            r#"{"x":0,"\u0078":1}"#,
            r#"{"a":{"x":1,"x":2}}"#,
            "NaN",
            "Infinity",
            "{} trailing",
            r#"{"a":"\ud800"}"#,
        ] {
            assert_eq!(
                post("application/json", text).reason,
                "uninspectable_request",
                "{text}"
            );
        }
        let at_limit = format!("{}0{}", "[".repeat(32), "]".repeat(32));
        assert_eq!(post("application/json", &at_limit).reason, "clean");
        let beyond = format!("{}0{}", "[".repeat(33), "]".repeat(33));
        assert_eq!(
            post("application/json", &beyond).reason,
            "uninspectable_request"
        );
        assert_eq!(
            inspect("POST", "/", "application/json", &[0xff]).reason,
            "uninspectable_request"
        );
    }
    #[test]
    fn form_repetition_and_embedded_json() {
        for text in [
            "x=person%40example.test&x=safe",
            "x=safe&x=person%40example.test",
            "person%40example.test=safe",
            "x=%7B%22email%22%3A%22person%5Cu0040example.test%22%7D",
            "x=%22%7B%5C%22email%5C%22%3A%5C%22person%5C%5Cu0040example.test%5C%22%7D%22",
        ] {
            assert_eq!(
                post("application/x-www-form-urlencoded", text).reason,
                "sensitive_data",
                "{text}"
            );
        }
        for text in [
            "x=%",
            "x=%GG",
            "x=%FF",
            "x",
            "x=1&",
            "x=%7Bbroken",
            "x=%7B%22a%22%3A1%2C%22a%22%3A2%7D",
        ] {
            assert_eq!(
                post("application/x-www-form-urlencoded", text).reason,
                "uninspectable_request",
                "{text}"
            );
        }
        assert_eq!(
            post("application/x-www-form-urlencoded", "x=&x=hello+world").reason,
            "clean"
        );
    }
    #[test]
    fn strict_media_and_bounds() {
        for kind in [
            "text/plain garbage",
            "text/plain;",
            "text/plain; charset=utf-8; CHARSET=utf8",
            "text/plain; charset=latin1",
            "text/plain, application/json",
            "multipart/form-data",
            "text/plain\r\n",
            "text/plain; charset=\"utf-8\"junk",
        ] {
            assert_eq!(
                post(kind, "hello").reason,
                "uninspectable_request",
                "{kind}"
            );
        }
        assert_eq!(
            post("TEXT/PLAIN; charset=\"UTF-8\"", "hello").reason,
            "clean"
        );
        assert_eq!(
            post("text/plain; charset=us-ascii", "한글").reason,
            "uninspectable_request"
        );
        assert_eq!(
            post(
                "application/x-www-form-urlencoded; charset=us-ascii",
                "x=%C3%A9"
            )
            .reason,
            "uninspectable_request"
        );
        assert_eq!(
            post("text/plain", &"a".repeat(MAX_BODY_BYTES)).reason,
            "clean"
        );
        assert_eq!(
            post("text/plain", &"a".repeat(MAX_BODY_BYTES + 1)).reason,
            "uninspectable_request"
        );
        assert_eq!(
            inspect("GET", &"a".repeat(MAX_PATH_BYTES + 1), "", b"").reason,
            "uninspectable_request"
        );
        assert_eq!(
            inspect("CONNECT", "/", "", b"").reason,
            "uninspectable_request"
        );
        assert_eq!(
            post(
                "application/x-www-form-urlencoded",
                &vec!["a=b"; MAX_FORM_FIELDS].join("&")
            )
            .reason,
            "clean"
        );
        assert_eq!(
            post(
                "application/x-www-form-urlencoded",
                &vec!["a=b"; MAX_FORM_FIELDS + 1].join("&")
            )
            .reason,
            "uninspectable_request"
        );
    }
}
