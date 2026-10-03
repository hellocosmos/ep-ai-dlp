//! Lifetime guard for a process explicitly handed to the capture runtime.
//! Closing the only job handle kills the protected process tree, including on crash.
#[cfg(windows)]
pub struct ProcessGuard(windows::Win32::Foundation::HANDLE);
#[cfg(windows)]
impl ProcessGuard {
    pub fn attach(pid: u32) -> anyhow::Result<Self> {
        use windows::Win32::{
            Foundation::CloseHandle,
            System::{JobObjects::*, Threading::*},
        };
        unsafe {
            let job = CreateJobObjectW(None, None)?;
            let guard = Self(job);
            let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const _,
                std::mem::size_of_val(&limits) as u32,
            )?;
            let process = OpenProcess(
                PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION,
                false,
                pid,
            )?;
            let result = AssignProcessToJobObject(job, process);
            let _ = CloseHandle(process);
            result?;
            Ok(guard)
        }
    }
}
#[cfg(windows)]
impl Drop for ProcessGuard {
    fn drop(&mut self) {
        unsafe {
            let _ = windows::Win32::Foundation::CloseHandle(self.0);
        }
    }
}
// This opaque kernel handle has no thread affinity and is never inherited.
#[cfg(windows)]
unsafe impl Send for ProcessGuard {}
