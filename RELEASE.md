### Building a release for vote:

1. Grab a clean checkout for safety.
2. Run: "git checkout ${BRANCH}" if needed to switch to branch of the intended release point.
3. Update the versions etc:
  - setup.py
4. Commit the changes, tag them.
  - Run: "git add ."
  - Run: 'git commit -m "update versions for ${TAG}"'
  - Run: 'git tag -m "tag ${TAG}" ${TAG}'
5. Run: "python setup.py sdist" to create the qpid-python-${VERSION}.tar.gz release archive in the dist/ subdir.
6. Create signature and checksum files for the archive:
  - e.g "gpg --detach-sign --armor qpid-python-${VERSION}.tar.gz"
  - e.g "sha512sum qpid-python-${VERSION}.tar.gz > qpid-python-${VERSION}.tar.gz.sha512"
7. Push branch changes and tag.
  - Also update versions to the applicable snapshot version for future work on it.
8. Commit artifacts to dist dev repo in https://dist.apache.org/repos/dist/dev/qpid/python/${TAG} dir.
9. Send vote email, provide links to dist dev repo and JIRA release notes.

### After a vote succeeds:

1. If needed, tag the RC bits with the final name/version.
2. Commit the artifacts to dist release repo in https://dist.apache.org/repos/dist/release/qpid/python/${VERSION} dir:
   - e.g: svn cp -m "add files for qpid-python-${VERSION}" https://dist.apache.org/repos/dist/dev/qpid/python/${TAG}/ https://dist.apache.org/repos/dist/release/qpid/python/${VERSION}/
3. Give the mirrors some time to distribute things. Usually 24hrs to be safe, less if needed.
   - https://www.apache.org/mirrors/ gives stats on mirror age + last check etc.
4. Update the website with release content.
5. Send release announcement email.
