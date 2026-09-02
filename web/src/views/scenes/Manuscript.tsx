/**
 * The book the world is for (§43).
 *
 * `work` and `chapter` have been in the schema since the first migration, and the last
 * commit taught a scene to sit in a chapter — while leaving no way to make one, so the
 * "where it sits in the book" field on the scene form appeared only for the seeded demo
 * and was invisible to everybody else. A half-finished feature is worse than an absent
 * one: the writer sees the shape of something they cannot use.
 *
 * Deliberately small. This is not an outliner and does not try to be — it is the place a
 * writer says "this is the book, these are its chapters", so that the scenes they were
 * already writing have somewhere to sit.
 */

import { useState } from 'react'
import { api } from '../../api'
import type { Work } from '../../api'
import { ErrorBox, Panel } from '../../components/common'

interface Props {
  works: Work[]
  onChange: () => void
  onOpenScene: (id: string) => void
}

export function Manuscript({ works, onChange, onOpenScene }: Props) {
  const [newBook, setNewBook] = useState('')
  const [addingTo, setAddingTo] = useState<string | null>(null)
  const [newChapter, setNewChapter] = useState('')
  const [error, setError] = useState<string | null>(null)

  const guard = async (work: () => Promise<unknown>) => {
    setError(null)
    try {
      await work()
      onChange()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const startBook = () =>
    guard(async () => {
      await api.addWork(newBook.trim())
      setNewBook('')
    })

  const startChapter = (workId: string) =>
    guard(async () => {
      const work = works.find((w) => w.id === workId)
      await api.addChapter(workId, newChapter.trim(), work?.chapters.length ?? 0)
      setNewChapter('')
      setAddingTo(null)
    })

  return (
    <Panel title="The book" count={works.length || undefined}>
      {error && <ErrorBox error={error} />}

      {works.length === 0 && (
        <p className="muted small">
          Nothing here yet. A book is only a place to put scenes in order — you can keep
          writing scenes without one, and give them chapters later.
        </p>
      )}

      {works.map((work) => (
        <div key={work.id} className="manuscript-work">
          <div className="entity-line" style={{ alignItems: 'baseline' }}>
            <span className="name serif">{work.title}</span>
            <span className="desc muted small">{work.kind}</span>
            <button onClick={() => { setAddingTo(work.id); setNewChapter('') }}>
              + a chapter
            </button>
          </div>

          {addingTo === work.id && (
            <div className="toolbar">
              <input autoFocus value={newChapter} placeholder="The Ford"
                     onChange={(e) => setNewChapter(e.target.value)}
                     onKeyDown={(e) => {
                       if (e.key === 'Enter' && newChapter.trim()) {
                         void startChapter(work.id)
                       }
                       if (e.key === 'Escape') setAddingTo(null)
                     }} />
              <button className="active" disabled={!newChapter.trim()}
                      onClick={() => void startChapter(work.id)}>Add it</button>
              <button onClick={() => setAddingTo(null)}>Cancel</button>
            </div>
          )}

          <ol className="clean small" style={{ marginLeft: 12 }}>
            {work.chapters.map((chapter) => (
              <li key={chapter.id} style={{ marginTop: 4 }}>
                <strong>{chapter.title}</strong>
                {chapter.scenes.length === 0 ? (
                  <span className="muted"> — nothing in it yet</span>
                ) : (
                  <ul className="clean" style={{ marginLeft: 12 }}>
                    {chapter.scenes.map((scene) => (
                      <li key={scene.id}>
                        <button className="link"
                                onClick={() => onOpenScene(scene.id)}>
                          {scene.title}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ol>
          {work.chapters.length === 0 && (
            <p className="muted small" style={{ marginLeft: 12 }}>
              No chapters yet.
            </p>
          )}
        </div>
      ))}

      <div className="toolbar" style={{ marginTop: 10 }}>
        <input value={newBook} placeholder="a new book…"
               onChange={(e) => setNewBook(e.target.value)}
               onKeyDown={(e) => {
                 if (e.key === 'Enter' && newBook.trim()) void startBook()
               }} />
        <button disabled={!newBook.trim()} onClick={() => void startBook()}>
          Start it
        </button>
        {works.length > 0 && works[0].loose_scenes > 0 && (
          <span className="small muted">
            {works[0].loose_scenes} scene{works[0].loose_scenes === 1 ? '' : 's'} not
            placed in a chapter yet.
          </span>
        )}
      </div>
    </Panel>
  )
}
