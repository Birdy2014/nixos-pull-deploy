use crate::logger::{LogLevel, log};
use git2::ErrorCode;
use git2::{BranchType, FetchOptions, FetchPrune, Oid, Repository, build::CheckoutBuilder};
use std::thread;
use std::time::Duration;

pub type Commit = Oid;

pub struct GitWrapper {
    repo: Repository,
}

impl GitWrapper {
    pub fn new(directory: &str, origin_url: &str) -> Result<Self, git2::Error> {
        match Repository::open(directory) {
            Ok(repo) => Ok(Self { repo }),
            Err(err) if err.code() == ErrorCode::NotFound => match Repository::init(directory) {
                Ok(repo) => {
                    repo.remote("origin", origin_url)?;
                    Ok(Self { repo })
                }
                Err(err) => Err(err),
            },
            Err(err) => Err(err),
        }
    }

    pub fn fetch(&self, retries: usize) -> Result<(), git2::Error> {
        let mut last_error = None;
        for attempt in 0..=retries {
            if attempt > 0 {
                log("No network connection - retrying", LogLevel::Warning);
                thread::sleep(Duration::from_secs(1));
            }
            match self.fetch_once() {
                Ok(()) => return Ok(()),
                Err(error) => {
                    log(&format!("git fetch failed: {error}"), LogLevel::Warning);
                    last_error = Some(error);
                }
            }
        }
        // `0..=retries` always runs at least once, so this is Some.
        Err(last_error.expect("at least one fetch attempt is made"))
    }

    fn fetch_once(&self) -> Result<(), git2::Error> {
        let mut remote = self.repo.find_remote("origin")?;
        let mut options = FetchOptions::new();
        options.prune(FetchPrune::On);
        remote.fetch::<&str>(&[], Some(&mut options), None)?;
        Ok(())
    }

    pub fn get_commit(&self, refname: &str) -> Option<Commit> {
        let commit = self
            .repo
            .revparse_single(refname)
            .ok()?
            .peel_to_commit()
            .ok()?;
        Some(commit.id())
    }

    pub fn get_commit_message(&self, commit: Commit) -> Result<String, git2::Error> {
        let commit = self.repo.find_commit(commit)?;
        let message = commit.message().unwrap_or("");
        Ok(message.to_owned())
    }

    pub fn is_ancestor(&self, ancestor: Commit, commit: Commit) -> Result<bool, git2::Error> {
        let mut walk = self.repo.revwalk()?;
        walk.push(commit)?;
        for oid in walk {
            if oid? == ancestor {
                return Ok(true);
            }
        }
        Ok(false)
    }

    pub fn get_base(&self, a: Commit, b: Commit) -> Result<Commit, git2::Error> {
        self.repo.merge_base(a, b)
    }

    pub fn set_branch_to(&self, branch: &str, target: Commit) -> Result<(), git2::Error> {
        let refname = format!("refs/heads/{branch}");
        match self.repo.find_reference(&refname) {
            Ok(mut reference) => reference.set_target(target, "reset branch to target")?,
            Err(_) => self
                .repo
                .reference(&refname, target, false, "create branch at target")?,
        };
        Ok(())
    }

    pub fn list_remote_branches(&self) -> Result<Vec<String>, git2::Error> {
        let mut branches = Vec::new();
        for entry in self.repo.branches(Some(BranchType::Remote))? {
            let (branch, _typ) = entry?;
            let Some(name) = branch.name()? else {
                continue;
            };
            if !name.starts_with("origin/") {
                continue;
            }
            let commit = branch.get().peel_to_commit()?;
            branches.push((name.to_owned(), commit.time().seconds()));
        }
        branches.sort_by_key(|(_, time)| std::cmp::Reverse(*time));
        Ok(branches.into_iter().map(|(name, _)| name).collect())
    }

    /// Also updates working tree
    pub fn checkout(&self, commit: Commit) -> Result<(), git2::Error> {
        let commit = self.repo.find_commit(commit)?;
        self.repo.set_head_detached(commit.id())?;
        let tree = commit.tree()?;
        let mut builder = CheckoutBuilder::new();
        builder.force();
        self.repo
            .checkout_tree(tree.as_object(), Some(&mut builder))?;
        Ok(())
    }

    pub fn get_distance(&self, a: &str, b: &str) -> Result<i64, git2::Error> {
        let a = self.repo.revparse_single(a)?.peel_to_commit()?.id();
        let b = self.repo.revparse_single(b)?.peel_to_commit()?.id();
        let mut walk = self.repo.revwalk()?;
        walk.push(b)?;
        walk.hide(a)?;
        Ok(walk.count() as i64)
    }

    pub fn path(&self) -> &str {
        self.repo
            .workdir()
            .unwrap()
            .to_str()
            .unwrap()
            .strip_suffix("/")
            .unwrap()
    }
}
