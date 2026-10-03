//! Deterministic detectors. Only rule identifiers leave this module.
use regex::Regex;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::LazyLock,
};

pub(crate) const RULE_IDS: &[&str] = &[
    "anthropic_key",
    "aws_access_key",
    "credit_card",
    "email",
    "github_token",
    "google_api_key",
    "kr_phone",
    "kr_rrn",
    "openai_key",
    "private_key",
    "slack_token",
];

struct Rule {
    id: &'static str,
    regex: Regex,
    digits_boundary: bool,
}
static RULES: LazyLock<Vec<Rule>> = LazyLock::new(|| {
    [
        ("kr_rrn", r"^\d{6}[-\s]?[0-9]\d{6}", true),
        ("credit_card", r"(?:[0-9][ -]?){12,18}[0-9]", true),
        ("anthropic_key", r"\bsk-ant-[A-Za-z0-9_\-]{20,}", false),
        (
            "openai_key",
            r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}",
            false,
        ),
        ("aws_access_key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", false),
        ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b", false),
        ("slack_token", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}", false),
        ("google_api_key", r"\bAIza[0-9A-Za-z_\-]{35}\b", false),
        ("kr_phone", r"^01[016789][-\s]?\d{3,4}[-\s]?\d{4}", true),
        (
            "email",
            r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
            false,
        ),
    ]
    .into_iter()
    .map(|(id, pattern, digits_boundary)| Rule {
        id,
        regex: Regex::new(pattern).expect("constant detector regex"),
        digits_boundary,
    })
    .collect()
});
static PEM: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"-----BEGIN ((?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY)-----")
        .expect("constant PEM regex")
});

// Unicode decimal-number runs from the locked regex-syntax Unicode tables.
// Every run contains consecutive 0..9 sets (mathematical digits span 50).
// Only candidate digits are converted, never API keys or arbitrary content.
const DECIMAL_RANGES: &[(char, char)] = &[
    ('0', '9'),
    ('٠', '٩'),
    ('۰', '۹'),
    ('߀', '߉'),
    ('०', '९'),
    ('০', '৯'),
    ('੦', '੯'),
    ('૦', '૯'),
    ('୦', '୯'),
    ('௦', '௯'),
    ('౦', '౯'),
    ('೦', '೯'),
    ('൦', '൯'),
    ('෦', '෯'),
    ('๐', '๙'),
    ('໐', '໙'),
    ('༠', '༩'),
    ('၀', '၉'),
    ('႐', '႙'),
    ('០', '៩'),
    ('᠐', '᠙'),
    ('᥆', '᥏'),
    ('᧐', '᧙'),
    ('᪀', '᪉'),
    ('᪐', '᪙'),
    ('᭐', '᭙'),
    ('᮰', '᮹'),
    ('᱀', '᱉'),
    ('᱐', '᱙'),
    ('꘠', '꘩'),
    ('꣐', '꣙'),
    ('꤀', '꤉'),
    ('꧐', '꧙'),
    ('꧰', '꧹'),
    ('꩐', '꩙'),
    ('꯰', '꯹'),
    ('０', '９'),
    ('𐒠', '𐒩'),
    ('𐴰', '𐴹'),
    ('𐵀', '𐵉'),
    ('𑁦', '𑁯'),
    ('𑃰', '𑃹'),
    ('𑄶', '𑄿'),
    ('𑇐', '𑇙'),
    ('𑋰', '𑋹'),
    ('𑑐', '𑑙'),
    ('𑓐', '𑓙'),
    ('𑙐', '𑙙'),
    ('𑛀', '𑛉'),
    ('𑛐', '𑛣'),
    ('𑜰', '𑜹'),
    ('𑣠', '𑣩'),
    ('𑥐', '𑥙'),
    ('𑯰', '𑯹'),
    ('𑱐', '𑱙'),
    ('𑵐', '𑵙'),
    ('𑶠', '𑶩'),
    ('𑽐', '𑽙'),
    ('𖄰', '𖄹'),
    ('𖩠', '𖩩'),
    ('𖫀', '𖫉'),
    ('𖭐', '𖭙'),
    ('𖵰', '𖵹'),
    ('𜳰', '𜳹'),
    ('𝟎', '𝟿'),
    ('𞅀', '𞅉'),
    ('𞋰', '𞋹'),
    ('𞓰', '𞓹'),
    ('𞗱', '𞗺'),
    ('𞥐', '𞥙'),
    ('🯰', '🯹'),
];
fn decimal_digit(c: char) -> Option<u32> {
    if c.is_ascii_digit() {
        return Some(c as u32 - '0' as u32);
    }
    let index = DECIMAL_RANGES.partition_point(|(first, _)| *first <= c);
    if index == 0 {
        return None;
    }
    let (first, last) = DECIMAL_RANGES[index - 1];
    (c <= last).then(|| (c as u32 - first as u32) % 10)
}
fn numeric_candidates<'a>(
    text: &'a str,
    pattern: &'a Regex,
) -> impl Iterator<Item = (usize, usize)> + 'a {
    text.char_indices().filter_map(move |(start, c)| {
        if decimal_digit(c).is_none()
            || text[..start]
                .chars()
                .next_back()
                .and_then(decimal_digit)
                .is_some()
        {
            return None;
        }
        // Patterns are anchored to this boundary-valid start. No rejected match
        // consumes the next candidate, and no failed attempt scans the suffix.
        let matched = pattern.find(&text[start..])?;
        let end = start + matched.end();
        if text[end..].chars().next().and_then(decimal_digit).is_some() {
            return None;
        }
        Some((start, end))
    })
}

fn luhn(digits: &[u32]) -> bool {
    if !(13..=19).contains(&digits.len()) || digits.iter().all(|d| *d == digits[0]) {
        return false;
    }
    digits
        .iter()
        .rev()
        .enumerate()
        .map(|(i, d)| {
            let n = if i % 2 == 1 { d * 2 } else { *d };
            if n > 9 { n - 9 } else { n }
        })
        .sum::<u32>()
        % 10
        == 0
}
fn rrn(value: &str) -> bool {
    let digits: Vec<u32> = value.chars().filter_map(decimal_digit).collect();
    if digits.len() != 13 {
        return false;
    }
    let year = match digits[6] {
        1 | 2 | 5 | 6 => 1900,
        3 | 4 | 7 | 8 => 2000,
        _ => 1800,
    } + digits[0] * 10
        + digits[1];
    let month = digits[2] * 10 + digits[3];
    let day = digits[4] * 10 + digits[5];
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let days = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if leap {
                29
            } else {
                28
            }
        }
        _ => 0,
    };
    day >= 1 && day <= days
}

// Find overlapping candidates rather than allowing an unrelated preceding
// number to consume a valid card in a greedy regular-expression match.
fn card_candidates(text: &str) -> Vec<(usize, usize)> {
    let mut candidates = Vec::new();
    for (start, c) in text.char_indices() {
        if decimal_digit(c).is_none()
            || text[..start]
                .chars()
                .next_back()
                .and_then(decimal_digit)
                .is_some()
        {
            continue;
        }
        let mut position = start;
        let mut digits = [0; 19];
        for count in 1..=19 {
            let Some(c) = text[position..].chars().next() else {
                break;
            };
            let Some(digit) = decimal_digit(c) else {
                break;
            };
            digits[count - 1] = digit;
            position += c.len_utf8();
            if count >= 13
                && text[position..]
                    .chars()
                    .next()
                    .and_then(decimal_digit)
                    .is_none()
                && luhn(&digits[..count])
            {
                candidates.push((start, position));
            }
            if matches!(text[position..].chars().next(), Some(' ' | '-')) {
                position += 1;
            }
        }
    }
    candidates
}

pub fn detect(text: &str) -> Vec<&'static str> {
    let mut ids = BTreeSet::new();
    let mut taken: BTreeMap<usize, usize> = BTreeMap::new();
    let overlaps = |spans: &BTreeMap<usize, usize>, start: usize, end: usize| {
        spans
            .range(..end)
            .next_back()
            .is_some_and(|(_, prior_end)| start < *prior_end)
    };
    for cap in PEM.captures_iter(text) {
        let start = cap.get(0).unwrap();
        if overlaps(&taken, start.start(), start.end()) {
            continue;
        }
        let end_marker = format!("-----END {}-----", &cap[1]);
        let end = text[start.end()..]
            .find(&end_marker)
            .map(|offset| start.end() + offset + end_marker.len())
            .unwrap_or(text.len());
        taken.insert(start.start(), end);
        ids.insert("private_key");
    }
    for rule in RULES.iter() {
        if rule.id == "credit_card" {
            for (start, end) in card_candidates(text) {
                if overlaps(&taken, start, end) {
                    continue;
                }
                taken.insert(start, end);
                ids.insert("credit_card");
            }
            continue;
        }
        if rule.digits_boundary {
            for (start, end) in numeric_candidates(text, &rule.regex) {
                if overlaps(&taken, start, end) || (rule.id == "kr_rrn" && !rrn(&text[start..end]))
                {
                    continue;
                }
                taken.insert(start, end);
                ids.insert(rule.id);
            }
            continue;
        }
        for m in rule.regex.find_iter(text) {
            if overlaps(&taken, m.start(), m.end()) {
                continue;
            }
            taken.insert(m.start(), m.end());
            ids.insert(rule.id);
        }
    }
    ids.into_iter().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn numeric_candidates_preserve_later_boundary_valid_matches() {
        for (text, rule) in [
            ("010 01012345678", "kr_phone"),
            ("123456 9001011234567", "kr_rrn"),
            ("010 010-1234-5678", "kr_phone"),
            ("123456 900101-1234567", "kr_rrn"),
            ("010 010-１２３４-５６７８", "kr_phone"),
            ("123456 ９００１０１-1２３４５６７", "kr_rrn"),
        ] {
            assert!(detect(text).contains(&rule), "missing {rule}: {text}");
        }
        for text in [
            "101012345678",
            "010123456789",
            "19001011234567",
            "90010112345678",
        ] {
            assert!(
                !detect(text)
                    .iter()
                    .any(|id| matches!(*id, "kr_phone" | "kr_rrn")),
                "invalid boundary: {text}"
            );
        }
    }
    #[test]
    fn unicode_decimal_candidates_keep_validation_and_family_boundaries() {
        for (text, rule) in [
            ("010-１２３４-５６７８", "kr_phone"),
            ("９００１０１-1２３４５６７", "kr_rrn"),
            ("４１１１１１１１１１１１１１１１", "credit_card"),
            ("010-١٢٣٤-٥٦٧٨", "kr_phone"),
            ("٩٠٠١٠١-1٢٣٤٥٦٧", "kr_rrn"),
            ("𝟜𝟙𝟙𝟙𝟙𝟙𝟙𝟙𝟙𝟙𝟙𝟙𝟙𝟙𝟙𝟙", "credit_card"),
        ] {
            assert!(detect(text).contains(&rule), "missing {rule}: {text}");
        }
        for text in [
            "９００２３０-1２３４５６７",
            "４１１１１１１１１１１１１１１２",
            "１１１１１１１１１１１１１１１１",
            "010-１２３４-５６７８９",
            "１９００１０１1２３４５６７",
            "１４１１１１１１１１１１１１１１１",
            "⁴¹¹¹¹¹¹¹¹¹¹¹¹¹¹¹",
        ] {
            assert!(detect(text).is_empty(), "invalid numeric candidate: {text}");
        }
        // Unicode numeric lookalikes do not make otherwise invalid API keys
        // valid: conversion is confined to the phone/RRN/card candidates.
        assert!(detect("AKIA１２３４５６７８９０１２３４５６").is_empty());
    }
    #[test]
    fn unicode_decimal_table_matches_locked_regex_properties() {
        let nd = Regex::new(r"^\d$").unwrap();
        for codepoint in 0..=0x10ffff {
            let Some(c) = char::from_u32(codepoint) else {
                continue;
            };
            let mut encoded = [0; 4];
            assert_eq!(
                decimal_digit(c).is_some(),
                nd.is_match(c.encode_utf8(&mut encoded)),
                "Unicode table drift at U+{codepoint:04X}"
            );
        }
        for (first, last) in DECIMAL_RANGES {
            for codepoint in *first as u32..=*last as u32 {
                assert_eq!(
                    decimal_digit(char::from_u32(codepoint).unwrap()),
                    Some((codepoint - *first as u32) % 10)
                );
            }
        }
    }
    #[test]
    fn long_numeric_runs_and_many_candidate_inputs_are_bounded() {
        let ascii_run = "1".repeat(crate::inspection::MAX_BODY_BYTES);
        assert!(detect(&ascii_run).is_empty());
        let fullwidth_run = "１".repeat(crate::inspection::MAX_BODY_BYTES / 3);
        assert!(detect(&fullwidth_run).is_empty());
        let alternating = "1 ".repeat(crate::inspection::MAX_BODY_BYTES / 2);
        assert!(detect(&alternating).is_empty());
        let repeating = "010 01012345678\n";
        let many = repeating.repeat(crate::inspection::MAX_BODY_BYTES / repeating.len());
        assert!(detect(&many).contains(&"kr_phone"));
        let invalid = "9002301234567\n";
        let many_invalid = invalid.repeat(crate::inspection::MAX_BODY_BYTES / invalid.len());
        assert!(detect(&many_invalid).is_empty());
    }
    #[test]
    fn dates_checksums_and_boundaries() {
        assert!(detect("900230-1234567").is_empty());
        assert!(detect("000229-3234567").contains(&"kr_rrn"));
        assert!(detect("000229-1234567").is_empty());
        assert!(detect("4111 1111 1111 1111").contains(&"credit_card"));
        assert!(detect("123456 4111111111111111").contains(&"credit_card"));
        assert!(detect("4111111111111112").is_empty());
        assert!(detect("1111111111111111").is_empty());
        assert!(detect("14111111111111111").is_empty());
        assert!(detect("010-1234-5678").contains(&"kr_phone"));
        assert!(detect("person+tag@example.test").contains(&"email"));
    }
    #[test]
    fn api_key_families_and_pem() {
        for (value, id) in [
            ("sk-ant-abcdefghijklmnopqrstuvwxyz", "anthropic_key"),
            ("sk-proj-abcdefghijklmnopqrstuvwxyz", "openai_key"),
            ("sk-svcacct-abcdefghijklmnopqrstuvwxyz", "openai_key"),
            ("AKIAABCDEFGHIJKLMNOP", "aws_access_key"),
            ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "github_token"),
            ("xoxb-1234567890123", "slack_token"),
            ("AIzaabcdefghijklmnopqrstuvwxyz012345678", "google_api_key"),
        ] {
            assert!(detect(value).contains(&id), "missing {id}");
        }
        assert_eq!(
            detect(
                "-----BEGIN RSA PRIVATE KEY-----\n4111111111111111\n-----END RSA PRIVATE KEY-----"
            ),
            vec!["private_key"]
        );
        assert_eq!(
            detect("-----BEGIN PRIVATE KEY-----\nincomplete"),
            vec!["private_key"]
        );
    }
}
